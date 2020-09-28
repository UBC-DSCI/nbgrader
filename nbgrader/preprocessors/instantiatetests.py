import os
import yaml
import jinja2 as j2
import re
from .. import utils
from traitlets import Bool, List, Integer, Unicode
from textwrap import dedent
from . import Execute
import secrets

try:
    from time import monotonic # Py 3
except ImportError:
    from time import time as monotonic # Py 2

class CellExecutionComplete(Exception):
    """
    Used as a control signal for cell execution across run_cell and
    process_message function calls. Raised when all execution requests
    are completed and no further messages are expected from the kernel
    over zeromq channels.
    """
    pass


class CellExecutionError(Exception):
    """
    Custom exception to propagate exceptions that are raised during
    notebook execution to the caller. This is mostly useful when
    using nbconvert as a library, since it allows to deal with
    failures gracefully.
    """
    def __init__(self, traceback):
        super(CellExecutionError, self).__init__(traceback)
        self.traceback = traceback

    def __str__(self):
        s = self.__unicode__()
        if not isinstance(s, str):
            s = s.encode('utf8', 'replace')
        return s

    def __unicode__(self):
        return self.traceback

    @classmethod
    def from_code_and_msg(cls, code, msg):
        """Instantiate from a code cell object and a message contents
        (message is either execute_reply or error)
        """
        tb = '\n'.join(msg.get('traceback', []))
        return cls(exec_err_msg.format(code=code, traceback=tb
                                      ))

exec_err_msg = u"""\
An error occurred while executing the following code:
------------------
{code}
------------------
{traceback}
"""

class InstantiateTests(Execute):

    tests = None

    autotest_filename = Unicode(
        "tests.yml",
        help="The filename where automatic testing code is stored"
    ).tag(config=True)

    autotest_delimiter = Unicode(
        "AUTOTEST",
        help="The delimiter prior to snippets to be autotested"
    ).tag(config=True)

    hashed_delimiter = Unicode(
        "HASHED",
        help="The delimiter prior to an autotest block if snippet results should be protected by a hash function"
    ).tag(config=True)

    use_salt = Bool(
        True,
        help="Whether to add a salt to digested answers"
    ).tag(config=True)

    enforce_metadata = Bool(
        True,
        help=dedent(
            """
            Whether or not to complain if cells containing autotest delimiters
            are not marked as grade cells. WARNING: disabling this will potentially cause
            things to break if you are using the full nbgrader pipeline. ONLY
            disable this option if you are only ever planning to use nbgrader
            assign.
            """
        )
    ).tag(config=True)

    comment_strs = {
        'ir' : '#',
        'python3' : '#'
    }

    sanitizers = {
        'ir' : lambda s : re.sub(r'\[\d+\]\s+', '', s).strip('"').strip("'"),
        'python3' : lambda s : s.strip('"').strip("'")
    }

    def preprocess_cell(self, cell, resources, index):
        #new_lines will store the replacement code after autotest template instantiation
        new_lines = []
 
        #first, run the cell normally
        cell, resources = super(InstantiateTests, self).preprocess_cell(cell, resources, index)

        #if it's not a code cell or it's empty, just return
        if cell.cell_type != 'code':
            return cell, resources

        # determine whether the cell is a grade cell
        is_grade = utils.is_grade(cell)

        #get the comment string for this language
        comment_str = self.comment_strs[resources['kernel_name']]

        #split the code lines into separate strings
        lines = cell.source.split("\n")

        
        tests_loaded = False

        for line in lines:

            #if the current line doesn't have the autotest_delimiter or is not a comment 
            #then just append the line to the new cell code and go to the next line
            if self.autotest_delimiter not in line or line.strip()[:len(comment_str)] != comment_str:
                new_lines.append(line)
                continue

            # there are autotests; we should check that it is a grading cell
            if not is_grade and self.enforce_metadata: 
                raise RuntimeError(
                   "Autotest region detected in a non-grade cell; "
                   "please make sure all autotest regions are within "
                   "'Autograder tests' cells."
                )
            
            self.log.debug('')
            self.log.debug('')
            self.log.debug('Autotest delimiter found on line. Preprocessing...')

            #the first time we run into an autotest delimiter, obtain the 
            #tests object from the tests.yml template file for the assignment
            #and append any setup code to the cell block we're in
            #also figure out what language we're using
            if not tests_loaded:
                self.log.debug('Loading tests template file')
                self._load_test_template_file(resources)
                if self.setup_code is not None:
                    new_lines.append(self.setup_code)
                    self._execute_code_snippet(self.setup_code)
                self.log.debug('Setting sanitizer for language ' + resources['kernel_name'])
                self.sanitizer = self.sanitizers.get(resources['kernel_name'], lambda x : x)
                tests_loaded = True

            #decide whether to use hashing based on whether the self.hashed_delimiter token appears in the line before the self.autotest_delimiter token
            use_hash = (self.hashed_delimiter in line[:line.find(self.autotest_delimiter)])
            if use_hash:
                self.log.debug('Hashing delimiter found, using template: ' + self.hash_template)
            else:
                self.log.debug('Hashing delimiter not found')
            
            #take everything after the autotest_delimiter as code snippets separated by semicolons
            snippets = [snip.strip() for snip in line[line.find(self.autotest_delimiter)+len(self.autotest_delimiter):].strip(';').split(';')]

            #print autotest snippets to log
            self.log.debug('Found snippets to autotest: ')
            for snippet in snippets:
                self.log.debug(snippet)

            #generate the test for each snippet
            for snippet in snippets:
                self.log.debug('Running autotest generation for snippet ' + snippet)

                #create a random salt for this test
                if use_hash:
                    salt = secrets.token_hex(8)
                    self.log.debug('Using salt: ' + salt)
                else:
                    salt = None

                #get the normalized(/hashed) template tests for this code snippet
                self.log.debug('Instantiating normalized'+('/hashed ' if use_hash else ' ')+ 'test templates based on type')
                instantiated_tests, test_values, fail_messages = self._instantiate_tests(snippet, salt)

                # add all the lines to the cell
                self.log.debug('Inserting test code into cell')
                template = j2.Environment(loader=j2.BaseLoader).from_string(self.check_template)
                for i in range(len(instantiated_tests)):
                    check_code = template.render(snippet=instantiated_tests[i], value=test_values[i], message=fail_messages[i])
                    self.log.debug('Test: ' + check_code)
                    new_lines.append(check_code)
                
                #add an empty line after this block of test code
                new_lines.append('')

        # replace the cell source
        cell.source = "\n".join(new_lines)

        return cell, resources

    def _load_test_template_file(self, resources):
        self.log.debug('loading template tests.yml...')
        try:
            with open(os.path.join(resources['metadata']['path'], self.autotest_filename), 'r') as tests_file:
                tests = yaml.safe_load(tests_file)
            self.log.debug(tests)
        except FileNotFoundError:
            #if there is no tests file, just create a default empty tests dict
            self.log.warning('No tests.yml file found. If AUTOTESTS appears in testing cells, an error will be thrown.')
            tests = {}
        except yaml.parser.ParserError as e:
            self.log.error('tests.yml contains invalid YAML code.')
            self.log.error(e.msg)
            raise

        #get the test templates
        self.test_templates_by_type = tests['templates']

        #get the test dispatch code template
        self.dispatch_template = tests['dispatch']

        #get the sucess message template
        self.success_template = tests['success']

        #get the hash code template
        self.hash_template = tests['hash']

        #get the hash code template
        self.check_template = tests['check']

        #get the hash code template
        self.normalize_template = tests['normalize']

        #get the setup code if it's there
        self.setup_code = tests.get('setup', None)

    def _instantiate_tests(self, snippet, salt = None):
        #get the type of the snippet output (used to dispatch autotest)
        template = j2.Environment(loader=j2.BaseLoader).from_string(self.dispatch_template)
        dispatch_code = template.render(snippet=snippet)
        dispatch_result = self._execute_code_snippet(dispatch_code)
        self.log.debug('Dispatch result returned by kernel: ' + dispatch_result)
        #get the test code; if the type isn't in our dict, just default to 'default'
        #if default isn't in the tests code, this will throw an error
        try:
            tests = self.test_templates_by_type.get(dispatch_result, self.test_templates_by_type['default'])
        except KeyError:
            self.log.error('tests.yml must contain a top-level "default" key with corresponding test code')
            raise
        try:
            test_templs = [t['test'] for t in tests]
            fail_msgs = [t['fail'] for t in tests]
        except KeyError:
            self.log.error('each type in tests.yml must have a list of dictionaries with a "test" and "fail" key')
            self.log.error('the "test" item should store the test template code, and the "fail" item should store a failure message')
            raise

        #normalize the templates
        normalized_templs = []
        for templ in test_templs:
            template = j2.Environment(loader=j2.BaseLoader).from_string(self.normalize_template)
            normalized_templs.append(template.render(snippet=templ))

        #hashify the templates
        processed_templs = []
        if salt is not None:
            for templ in normalized_templs:
                template = j2.Environment(loader=j2.BaseLoader).from_string(self.hash_template)
                processed_templs.append(template.render(snippet=templ, salt=salt))
        else:
            processed_templs = normalized_templs

        #instantiate and evaluate the tests
        instantiated_tests = []
        test_values = []
        for templ in processed_templs:
            #instantiate the template snippet
            template = j2.Environment(loader=j2.BaseLoader).from_string(templ)
            instantiated_test = template.render(snippet=snippet)
            #run the instantiated template code
            test_value = self._execute_code_snippet(instantiated_test)
            instantiated_tests.append(instantiated_test)
            test_values.append(test_value)

        return instantiated_tests, test_values, fail_msgs

    #adapted from nbconvert.ExecutePreprocessor.run_cell
    def _execute_code_snippet(self, code):
        parent_msg_id = self.kc.execute(code, stop_on_error=not self.allow_errors)
        self.log.debug("Executing command for autotest generation:\n%s", code)
        deadline = None
        if self.timeout is not None:
            deadline = monotonic() + self.timeout
        
        more_output = True
        # polling_exec_reply=true => continue to poll the shell_channel
        polling_exec_reply = True

        while more_output or polling_exec_reply:
            if polling_exec_reply:
                if self._passed_deadline(deadline):
                    polling_exec_reply = False
                    continue

                # Avoid exceeding the execution timeout (deadline), but stop
                # after at most 1s so we can poll output from iopub_channel.
                timeout = self._timeout_with_deadline(1, deadline)
                exec_reply = self._poll_for_reply(parent_msg_id, cell=None, timeout=timeout)
                if exec_reply is not None:
                    polling_exec_reply = False

            if more_output:
                try:
                    timeout = self.iopub_timeout
                    if polling_exec_reply:
                        # Avoid exceeding the execution timeout (deadline) while
                        # polling for output.
                        timeout = self._timeout_with_deadline(timeout, deadline)
                    msg = self.kc.iopub_channel.get_msg(timeout=timeout)
                except Empty:
                    if polling_exec_reply:
                        # Still waiting for execution to finish so we expect that
                        # output may not always be produced yet.
                        continue

                    if self.raise_on_iopub_timeout:
                        raise TimeoutError("Timeout waiting for IOPub output")
                    else:
                        self.log.warning("Timeout waiting for IOPub output")
                        more_output = False
                        continue

            if msg['parent_header'].get('msg_id') != parent_msg_id:
                # not an output from our execution
                continue

            #process the message
            try:
                msg_type = msg['msg_type']
                content = msg['content']

                if msg_type in {'execute_result', 'display_data', 'update_display_data'}:
                    self.log.debug("execute result: %s", content)
                    return self.sanitizer(content['data']['text/plain'])

                if msg_type == 'error':
                    self.log.debug("execute error: %s", content)
                    raise CellExecutionError.from_code_and_msg(code, content)

                if msg_type == 'status':
                    if content['execution_state'] == 'idle':
                        raise CellExecutionComplete()
            except CellExecutionComplete:
                 more_output = False
        return None
