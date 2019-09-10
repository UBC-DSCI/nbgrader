import os
import json
from traitlets import Bool, List, Integer
from textwrap import dedent
from . import Execute

try:
    from time import monotonic # Py 3
except ImportError:
    from time import time as monotonic # Py 2


def get_type_code(kernel_name):
    if kernel_name == 'ir': 
        return lambda var_name : "class(" + var_name + ")"
    elif kernel_name == 'python3':
        return lambda var_name : "type(" + var_name +")"
    else:
        raise NotImplementedError("NbGrader AUTOTEST not implemented for kernels other than 'ir' and 'python3'.")

class InstantiateTests(Execute):

    autotest_filename = Unicode(
        "tests.json",
        help="The filename where automatic testing code is stored"
    ).tag(config=True)

    autotest_delimiter = Unicode(
        "AUTOTEST",
        help="The delimiter prior to a variable to be autotested"
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

    def __init__(self, **kw):
        #run the parent constructor
        super(InstantiateTests, self).__init__(**kw)
        #load the autotests template file
        assignment_folder = self._format_source(self, assignment_id, student_id, escape=False)
        try:
            with open(os.path.join(assignment_folder, self.autotest_filename), 'r') as tests_file:
                self.tests = json.load(tests_file)
        except FileNotFoundError:
            #if there is no tests file, just create a default empty tests dict
            self.log.warning('InstantiateTests preprocessor: no tests.json file found. Defaulting to empty tests dict')
            self.tests = {}
        except json.JSONDecodeError as e:
            self.log.error('InstantiateTests preprocessor: tests.json contains invalid JSON code.')
            self.log.error(e.msg)
            raise
            
       
        
    def preprocess_cell(self, cell, resources, index):

        #first, run the cell normally
        cell, resources = super(InstantiateTests).preprocess_cell(cell, resources, index)

        #if it's not a code cell or it's empty, just return
        if cell.cell_type != 'code':
            return cell, resources

        # determine whether the cell is a grade cell
        is_grade = utils.is_grade(cell)

        #get the function that creates the right "type" function for the language that's running
        #might need this... self.kernel_name = self.nb.metadata['kernelspec']['name']
        type_code = get_type_code(self.kernel_name)

        #split the code lines into separate strings
        lines = cell.source.split("\n")
        new_lines = []
        for line in lines:

            #if the current line doesn't have the autotest_delimiter or is not a comment 
            #then just append the line to the new cell code and go to the next line
            if autotest_delimiter not in line or line[0] != '#':
                new_lines.append(line)
                continue

            # there are autotests; we should check that it is a grading cell
            if not is_grade and self.enforce_metadata: 
                raise RuntimeError(
                   "Autotest region detected in a non-grade cell; "
                   "please make sure all autotest regions are within "
                   "'Autograder tests' cells."
                )
            
            #take everything after the autotest_delimiter and split by commas/spaces
            #these are expected to be variable names
            var_names = line[line.find(autotest_delimiter)+len(autotest_delimiter):].split(" ,")
 
            #get the type of each variable (will be used to dispatch autotest code)
            var_names_types = [(var, self._execute_code(type_code(var))) for var in var_names]

            #
        
       

        #extract lines for autotested variables
        varnames = _pull_autotest_vars(self, cell)

        


        #run code required to instantiate each autotest
        #insert the test back into the notebook
        return cell, resources

    def _instantiate_test(self, cell):
        pass

    def _insert_instantiated_test_code(self, cell):
        pass

    #adapted from nbconvert.ExecutePreprocessor.run_cell
    def _execute_code(self, code):
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
                    return content

                if msg_type == 'status':
                    if content['execution_state'] == 'idle':
                        raise CellExecutionComplete()

             except CellExecutionComplete:
                 more_output = False
        return None
