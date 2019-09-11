import os
import json
import jinja2 as j2
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

def get_type_snippet(kernel_name):
    if kernel_name == 'ir': 
        return lambda var_name : "class(" + var_name + ")"
    elif kernel_name == 'python3':
        return lambda var_name : "type(" + var_name +")"
    else:
        raise NotImplementedError("NbGrader AUTOTEST not implemented for kernels other than 'ir' and 'python3'.")

class InstantiateTests(Execute):

    tests = None

    autotest_filename = Unicode(
        "tests.json",
        help="The filename where automatic testing code is stored"
    ).tag(config=True)

    autotest_delimiter = Unicode(
        "AUTOTEST",
        help="The delimiter prior to a variable to be autotested"
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
            if self.autotest_delimiter not in line or line[0] != '#':
                new_lines.append(line)
                continue

            # there are autotests; we should check that it is a grading cell
            if not is_grade and self.enforce_metadata: 
                raise RuntimeError(
                   "Autotest region detected in a non-grade cell; "
                   "please make sure all autotest regions are within "
                   "'Autograder tests' cells."
                )

            #the first time we run into an autotest delimiter, obtain the 
            #tests object from the tests.json template file for the assignment
            #and append any setup code to the cell block we're in
            if self.tests is None:
                self._load_test_templates(resources)
                if 'setup' in self.tests:
                    new_lines.append(self.tests['setup'])
                 
            
            #take everything after the autotest_delimiter as a code snippet
            snippet = line[line.find(self.autotest_delimiter)+len(self.autotest_delimiter):].strip()
            self.log.debug('Found snippet to autotest: ' + snippet)

            test_template_code, test_answer_code = self._get_templates(snippet)
            
            #create a random salt for this test
            salt = secrets.token_hex(8)
            self.log.debug('Salt: ' + salt)

            #evaluate everything needed to instantiate the test
            test_answers = {}
            self.log.debug('Getting template answers') 

            for template_varname, template_snippet in test_answer_code.items():
                self.log.debug('Template variable name: ' + template_varname)
                self.log.debug('Template snippet: ' + template_snippet)

                #evaluate the template snippet needed to instantiate the template
                test_answers[template_varname] = self._evaluate_template_snippet(snippet, template_snippet, salt)
                

            #instantiate the overall test template
            template = j2.Environment(loader=j2.BaseLoader).from_string(test_template_code)
            instantiated_test = template.render(snippet=snippet, salt=salt, **test_answers)
            self.log.debug('Instantiated test: ' + instantiated_test)

            #add lines of code to the cell 
            new_lines.extend(instantiated_test.split('\n'))
            
            #add an empty line after this block of test code
            new_lines.append('')

        # replace the cell source
        cell.source = "\n".join(new_lines)

        return cell, resources

    def _load_test_templates(self, resources):
        self.log.debug('loading template tests.json...')
        try:
            with open(os.path.join(resources['metadata']['path'], self.autotest_filename), 'r') as tests_file:
                self.tests = json.load(tests_file)
            self.log.debug(self.tests)
        except FileNotFoundError:
            #if there is no tests file, just create a default empty tests dict
            self.log.warning('No tests.json file found. Defaulting to empty tests dict')
            self.tests = {}
        except json.JSONDecodeError as e:
            self.log.error('tests.json contains invalid JSON code.')
            self.log.error(e.msg)
            raise
        #get the function that creates the right "type" function for the language that's running
        self.type_code = get_type_snippet(self.kernel_name)
        #if there is setup code, run it now
        if 'setup' in self.tests:
            self._execute_code_snippet(self.tests['setup'])

    def _get_templates(self, snippet):
        #get the type of the snippet output (used to dispatch autotest)
        snippet_type = self._execute_code_snippet(self.type_code(snippet)) 
        self.log.debug('Snippet type returned by kernel: ' + snippet_type)
        #get the test code; if the type isn't in our dict, just default to 'default'
        #if default isn't in the tests code, this will throw an error
        try:
            if snippet_type in self.tests:
                test = self.tests[snippet_type] 
            else: 
                test = self.tests['default']
        except KeyError:
            self.log.error('tests.json must contain a top-level "default" key with corresponding test code')
            raise
        try:
            test_template_code = test['template']
            test_answer_code = test['answers']
        except KeyError:
            self.log.error('each type in tests.json must have a "template" and "answers" item')
            self.log.error('the "template" item should store the test template code, and the "answers" item should store a dict of template variable names and snippets to run')
            raise
        return test_template_code, test_answer_code

    def _evaluate_template_snippet(self, snippet, template_snippet, salt):
        #instantiate the template snippet
        template = j2.Environment(loader=j2.BaseLoader).from_string(template_snippet)
        instantiated_snippet = template.render(snippet=snippet, salt=salt)
        #run the instantiated template code
        output = self._execute_code_snippet(instantiated_snippet)
        self.log.debug('Instantiated test snippet: ' + instantiated_snippet)
        self.log.debug('Output: ' + output)
        return output

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
                    return content['data']['text/plain']

                if msg_type == 'error':
                    raise CellExecutionError.from_code_and_msg(code, content)

                if msg_type == 'status':
                    if content['execution_state'] == 'idle':
                        raise CellExecutionComplete()
            except CellExecutionComplete:
                 more_output = False
        return None
