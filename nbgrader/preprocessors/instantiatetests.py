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


def sanitize_R_output(out):
    out = re.sub(r'\[\d+\]\s+', '', out)
    return out.strip('"').strip("'")

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

    output_sanitizers = {
        'ir' : sanitize_R_output
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

        #split the code lines into separate strings
        lines = cell.source.split("\n")
        for line in lines:

            #if the current line doesn't have the autotest_delimiter or is not a comment 
            #then just append the line to the new cell code and go to the next line
            if self.autotest_delimiter not in line or line.strip()[0] != '#':
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
            if self.tests is None:
                self.log.debug('Loading tests template file')
                self._load_test_template_file(resources)
                if 'setup' in self.tests:
                    new_lines.append(self.tests['setup'])
                self.log.debug('Setting sanitizer for language ' + resources['kernel_name'])
                self.sanitizer = self.output_sanitizers.get(resources['kernel_name'], lambda x : x)

            #decide whether to use hashing based on whether the self.hashed_delimiter token appears in the line before the self.autotest_delimiter token
            use_hash = self.hashed_delimiter in line[:line.find(self.autotest_delimiter)]
            self.log.debug('Hashing delimiter ' + str('' if use_hash else 'not ') + 'found')
            
            #take everything after the autotest_delimiter as code snippets separated by semicolons
            snippets = [snip.strip() for snip in line[line.find(self.autotest_delimiter)+len(self.autotest_delimiter):].split(';')]

            #print autotest snippets to log
            self.log.debug('Found snippets to autotest: ')
            for snippet in snippets:
                self.log.debug(snippet)

            #generate the test for each snippet
            for snippet in snippets:
                self.log.debug('Running autotest generation for snippet ' + snippet)

                self.log.debug('Getting templates')
                test_template, variable_snippets, hash_snippet = self._get_templates(snippet, use_hash)

                #create a random salt for this test
                salt = secrets.token_hex(8)
                if use_hash:
                    self.log.debug('Salt: ' + salt) 

                #evaluate everything needed to instantiate the test
                test_variables = {}
                self.log.debug('Evaluating variable templates')
                for variable_name, variable_snippet in variable_snippets.items():
                    self.log.debug('Template variable name: ' + variable_name)
                    self.log.debug('Template snippet: ' + variable_snippet)

                    #instantiate the variable snippet and evaluate it
                    test_variables[variable_name] = {}
                    test_variables[variable_name]['code'], test_variables[variable_name]['val'] = self._evaluate_variable_snippet(snippet, variable_snippet, salt, hash_snippet)
                    
                    self.log.debug('Variable code: ' + str(test_variables[variable_name]['code']))
                    self.log.debug('Variable value: ' + str(test_variables[variable_name]['val']))

                #instantiate the overall test template
                self.log.debug('Evaluating test template')
                template = j2.Environment(loader=j2.BaseLoader).from_string(test_template)
                instantiated_test = template.render(snippet=snippet, **test_variables)
                self.log.debug('Instantiated test:\n' + instantiated_test)

                #add lines of code to the cell 
                new_lines.extend(instantiated_test.split('\n'))
                
                #add an empty line after this block of test code
                new_lines.append('')

        # replace the cell source
        cell.source = "\n".join(new_lines)

        return cell, resources

    def _load_test_template_file(self, resources):
        self.log.debug('loading template tests.yml...')
        try:
            with open(os.path.join(resources['metadata']['path'], self.autotest_filename), 'r') as tests_file:
                self.tests = yaml.safe_load(tests_file)
            self.log.debug(self.tests)
        except FileNotFoundError:
            #if there is no tests file, just create a default empty tests dict
            self.log.warning('No tests.yml file found. If AUTOTESTS appears in testing cells, an error will be thrown.')
            self.tests = {}
        except yaml.parser.ParserError as e:
            self.log.error('tests.yml contains invalid YAML code.')
            self.log.error(e.msg)
            raise
        #get the test dispatch code template
        self.dispatch_template = self.tests['dispatch']
        #if there is setup code, run it now
        if 'setup' in self.tests:
            self._execute_code_snippet(self.tests['setup'])

    def _get_templates(self, snippet, use_hash):
        #get the type of the snippet output (used to dispatch autotest)
        template = j2.Environment(loader=j2.BaseLoader).from_string(self.dispatch_template)
        dispatch_code = template.render(snippet=snippet)
        dispatch_result = self._execute_code_snippet(dispatch_code)
        self.log.debug('Dispatch result returned by kernel: ' + dispatch_result)
        #get the test code; if the type isn't in our dict, just default to 'default'
        #if default isn't in the tests code, this will throw an error
        try:
            test = self.tests['templates'].get(dispatch_result, self.tests['templates']['default'])
        except KeyError:
            self.log.error('tests.yml must contain a top-level "default" key with corresponding test code')
            raise
        try:
            test_template = test['test']
            variable_snippets = test['variables']
            if use_hash:
                hash_snippet = test['hash']
            else:
                hash_snippet = None
        except KeyError:
            self.log.error('each type in tests.yml must have a "test" and "variables" item')
            self.log.error('the "test" item should store the test template code, and the "variables" item should store a dict of template variable names and corresponding code to run')
            self.log.error('if hashing is requested, the test item should also have a "hash" item storing hashing code')
            raise
        return test_template, variable_snippets, hash_snippet

    def _evaluate_variable_snippet(self, snippet, variable_snippet, salt, hash_snippet):
        #first, if things are being hashed, replace snippet variable in the template_snippet with the hash_snippet
        if hash_snippet is not None:
            #substitute the hash instantiate the template snippet
            template = j2.Environment(loader=j2.BaseLoader).from_string(hash_snippet)
            preprocessed_snippet = template.render(snippet=variable_snippet, salt=salt)   
            self.log.debug('Variable snippet with hash template and salt inserted: ' + preprocessed_snippet)
        else:
            preprocessed_snippet = variable_snippet

        #instantiate the template snippet
        template = j2.Environment(loader=j2.BaseLoader).from_string(preprocessed_snippet)
        instantiated_snippet = template.render(snippet=snippet)
        #run the instantiated template code
        variable_value = self._execute_code_snippet(instantiated_snippet)
        self.log.debug('Instantiated variable snippet: ' + instantiated_snippet)
        self.log.debug('Variable value: ' + variable_value)
        return instantiated_snippet, variable_value

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
                self.log.debug("msg_type: %s", msg_type)
                content = msg['content']
                self.log.debug("content: %s", content)

                if msg_type in {'execute_result', 'display_data', 'update_display_data'}:
                    return self.sanitizer(content['data']['text/plain'])

                if msg_type == 'error':
                    raise CellExecutionError.from_code_and_msg(code, content)

                if msg_type == 'status':
                    if content['execution_state'] == 'idle':
                        raise CellExecutionComplete()
            except CellExecutionComplete:
                 more_output = False
        return None
