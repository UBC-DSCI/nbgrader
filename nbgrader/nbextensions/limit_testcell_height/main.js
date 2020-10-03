define([
    'base/js/namespace',
    'base/js/events'
    ], function(Jupyter, events) {

    var limit_testcell_heights() {
        cells = Jupyter.notebook.get_cells();
        for (var i=0; i < cells.length; i++) {
            if (cells[i].metadata.nbgrader !== undefined && cells[i].metadata.nbgrader.hasOwnProperty("max_height")) {
                mh = cells[i].metadata.nbgrader.max_height
                var code = cells[i].element.find(".CodeMirror")[0].CodeMirror;
                  code.options.fold = true;
                  code.setSize(null, mh);
            }
        }
    }
    // Run on start
    function load_ipython_extension() {
        limit_testcall_heights();
    }
    return {
        load_ipython_extension: load_ipython_extension
    };
});
