# coding: utf-8

import sys

from traitlets import default

from .baseapp import NbGrader, nbgrader_aliases, nbgrader_flags
from ..converters import BaseConverter, GenerateTests, NbGraderException

aliases = {
    'course': 'CourseDirectory.course_id'
}
aliases.update(nbgrader_aliases)
del aliases['student']
aliases.update({
})

flags = {}
flags.update(nbgrader_flags)
flags.update({
    'no-db': (
        {
            'SaveCells': {'enabled': False},
            'GenerateAssignment': {'no_database': True}
        },
        "Do not save information into the database."
    ),
    'create': (
        {'GenerateAssignment': {'create_assignment': True}},
        "Deprecated: Create an entry for the assignment in the database, if one does not already exist. "
        "This is now the default."
    ),
    'force': (
        {'BaseConverter': {'force': True}},
        "Overwrite an assignment/submission if it already exists."
    ),
    'f': (
        {'BaseConverter': {'force': True}},
        "Overwrite an assignment/submission if it already exists."
    ),
})


class GenerateTestsApp(NbGrader):

    name = u'nbgrader-generate-tests'
    description = u'Produce the source version of an assignment, filling in any template tests.'

    aliases = aliases
    flags = flags

    examples = """
        Produce the source version of an assignment, filling in any template tests.
        This performs several modifications to a template assignment:
          
            ##########################################################
            ### TODO fix the below description for test generation ###
            ##########################################################

            1. It inserts a header and/or footer to each notebook in the
               assignment, if the header/footer are specified.

            2. It locks certain cells so that they cannot be deleted by students
               accidentally (or on purpose!)

            3. It removes solutions from the notebooks and replaces them with
               code or text stubs saying (for example) "YOUR ANSWER HERE".

            4. It clears all outputs from the cells of the notebooks.

            5. It saves information about the cell contents so that we can warn
               students if they have changed the tests, or if they have failed
               to provide a response to a written answer. Specifically, this is
               done by computing a checksum of the cell contents and saving it
               into the cell metadata.

            6. It saves the tests used to grade students' code into a database,
               so that those tests can be replaced during autograding if they
               were modified by the student (you can prevent this by passing the
               --no-db flag).

               If the assignment is not already present in the database, it
               will be automatically created when running `nbgrader generate_assignment`.

        `nbgrader generate_tests` takes one argument (the name of the assignment), and
        looks for notebooks in the 'template' directory by default, according to
        the directory structure specified in `CourseDirectory.directory_structure`.
        The version with templates all filled in is saved into the 'source' directory.

        Note that the directory structure requires the `student_id` to be given;
        however, there is no student ID at this point in the process. Instead,
        `nbgrader generate_tests` sets the student ID to be '.' so by default, files are
        read in according to:

            template/./{assignment_id}/{notebook_id}.ipynb

        and saved according to:

            source/./{assignment_id}/{notebook_id}.ipynb

        """

    @default("classes")
    def _classes_default(self):
        classes = super(GenerateTestsApp, self)._classes_default()
        classes.extend([BaseConverter, GenerateTests])
        return classes

    def start(self):
        super(GenerateTestsApp, self).start()

        if len(self.extra_args) > 1:
            self.fail("Only one argument (the assignment id) may be specified")
        elif len(self.extra_args) == 1 and self.coursedir.assignment_id != "":
            self.fail("The assignment cannot both be specified in config and as an argument")
        elif len(self.extra_args) == 0 and self.coursedir.assignment_id == "":
            self.fail("An assignment id must be specified, either as an argument or with --assignment")
        elif len(self.extra_args) == 1:
            self.coursedir.assignment_id = self.extra_args[0]

        converter = GenerateTests(coursedir=self.coursedir, parent=self)
        try:
            converter.start()
        except NbGraderException:
            sys.exit(1)
