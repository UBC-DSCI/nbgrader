define([
    'base/js/namespace',
    'base/js/events'
    ], function(Jupyter, events) {

    var limit_cell_heights() {
        console.log('LOADED THE EXTENSION PROPERLY')
        cells = Jupyter.notebook.get_cells();
        for (var i=0; i < cells.length; i++) {
            if (cells[i].metadata.max_height !== undefined) {
                mh = cells[i].metadata.nbgrader.max_height
                var code = cells[i].element.find(".CodeMirror")[0].CodeMirror;
                  code.options.fold = true;
                  code.setSize(null, mh);
            }
        }
    }
    // Run on start
    function load_ipython_extension() {
        limit_cell_heights();
    }
    return {
        load_ipython_extension: load_ipython_extension
    };
});
