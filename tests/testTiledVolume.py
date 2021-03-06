import os
import sys
import tempfile
import numpy
import h5py
import copy
import SimpleHTTPServer
import SocketServer

from lazyflow.utility.io.tiledVolume import TiledVolume
from lazyflow.utility import PathComponents, export_to_tiles
from lazyflow.utility.jsonConfig import JsonConfigParser
from lazyflow.roi import roiToSlice

# See 'main' section below for logging configuration.
import logging
logger = logging.getLogger(__file__)

ENABLE_SERVER_LOGGING = False

volume_description_text = \
"""
{
    "_schema_name" : "tiled-volume-description",
    "_schema_version" : 1.0,
 
    "name" : "My Tiled Data",
    "format" : "png",
    "dtype" : "uint8",
    "bounds_zyx" : [100, 600, 600], 
 
    "tile_shape_2d_yx" : [200,200],
 
    "tile_url_format" : "http://localhost:8000/tile_z{z_start:05}_y{y_start:05}_x{x_start:05}.png",
    "extend_slices" : [ [40, [41]],
                        [44, [45, 46, 47]] ]
}
"""

class DataSetup(object):
    """
    Class to encapsulate the functions that create test data for tiled volume tests.
    Can also be used by other test modules (e.g. testOpTiledVolumeReader.py)
    """
    def __init__(self):
        self.TILE_DIRECTORY = None
        self.REFERENCE_VOL_FILE = None
        self.REFERENCE_VOL_PATH = None
        self.VOLUME_DESCRIPTION_FILE = None
        self.TRANSPOSED_VOLUME_DESCRIPTION_FILE = None
        
        self.teardown = lambda: None

    def setup(self):
        """
        Generate a directory with all the files needed for this test.
        We use the same temporary directory every time, so we don't 
        waste time regenerating the data if the test has already been run recently.
        
        The directory consists of the following files:
        - reference_volume.h5
        - volume_description.json
        - transposed_volume_description.json
        - [lots of png tiles..]
        """        
        tmp = tempfile.gettempdir()
        self.TILE_DIRECTORY = os.path.join( tmp, 'testTiledVolume_data' )
        logger.debug("Using test directory: {}".format( self.TILE_DIRECTORY ))
        self.REFERENCE_VOL_PATH = os.path.join( self.TILE_DIRECTORY, 'reference_volume.h5/data' )
        ref_vol_path_comp = PathComponents(self.REFERENCE_VOL_PATH)
        self.REFERENCE_VOL_FILE = ref_vol_path_comp.externalPath
        self.VOLUME_DESCRIPTION_FILE = os.path.join( self.TILE_DIRECTORY, 'volume_description.json' )
        self.TRANSPOSED_VOLUME_DESCRIPTION_FILE = os.path.join( self.TILE_DIRECTORY, 'transposed_volume_description.json' )
    
        if not os.path.exists(self.TILE_DIRECTORY):
            print "Creating new tile directory: {}".format( self.TILE_DIRECTORY )
            os.mkdir(self.TILE_DIRECTORY)
    
        if not os.path.exists(self.REFERENCE_VOL_FILE):
            ref_vol = numpy.random.randint(0,255, (100,600,600) ).astype(numpy.uint8)
            with h5py.File(self.REFERENCE_VOL_FILE, 'w') as ref_file:
                ref_file[ref_vol_path_comp.internalPath] = ref_vol
        else:
            with h5py.File(self.REFERENCE_VOL_FILE, 'r') as ref_file:
                ref_vol = ref_file[ref_vol_path_comp.internalPath][:] 
    
        need_rewrite = False
        if not os.path.exists( self.VOLUME_DESCRIPTION_FILE ):
            need_rewrite = True
        else:
            with open(self.VOLUME_DESCRIPTION_FILE, 'r') as f:
                if f.read() != volume_description_text:
                    need_rewrite = True
        
        if need_rewrite:
            with open(self.VOLUME_DESCRIPTION_FILE, 'w') as f:
                f.write(volume_description_text)
    
            # Read the volume description as a JsonConfig Namespace
            volume_description = TiledVolume.readDescription(self.VOLUME_DESCRIPTION_FILE)
    
            # Write out a copy of the description, but with custom output axes
            config_helper = JsonConfigParser( TiledVolume.DescriptionFields )
            transposed_description = copy.copy(volume_description)
            transposed_description.output_axes = "xyz"
            config_helper.writeConfigFile(self.TRANSPOSED_VOLUME_DESCRIPTION_FILE, transposed_description)
    
            # Remove all old image tiles in the tile directory
            files = os.listdir(self.TILE_DIRECTORY)
            for name in files:
                if os.path.splitext(name)[1] == '.' + volume_description.format:
                    os.remove( os.path.join(self.TILE_DIRECTORY, name) )
    
            # Write the new tiles
            export_to_tiles( ref_vol, volume_description.tile_shape_2d_yx[0], self.TILE_DIRECTORY, print_progress=False )    
    
            # To support testMissingTiles (below), remove slice 2
            files = os.listdir(self.TILE_DIRECTORY)
            for name in files:
                if name.startswith("tile_z00002"):
                    p = os.path.join(self.TILE_DIRECTORY, name)
                    print "removing:", p
                    os.remove( p )
                
    
        # lastly, start the server
        self._start_server()
    
    def _start_server(self):
        original_cwd = os.getcwd()
        os.chdir(self.TILE_DIRECTORY)
        class Handler(SimpleHTTPServer.SimpleHTTPRequestHandler):
            def log_request(self, *args, **kwargs):
                if ENABLE_SERVER_LOGGING:
                    SimpleHTTPServer.SimpleHTTPRequestHandler.log_request( self, *args, **kwargs )
    
            def log_error(self, *args, **kwargs):
                if ENABLE_SERVER_LOGGING:
                    SimpleHTTPServer.SimpleHTTPRequestHandler.log_error( self, *args, **kwargs )
        
        class Server(SocketServer.TCPServer):
            # http://stackoverflow.com/questions/10613977/a-simple-python-server-using-simplehttpserver-and-socketserver-how-do-i-close-t
            allow_reuse_address = True
        server = Server(("", 8000), Handler)
        import threading
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.start()
        
        def shutdown_server():
            logger.debug("Shutting down server...")
            server.shutdown()
            server.server_close()
            os.chdir(original_cwd)
            server_thread.join()
    
        # Set the module teardown function so nosetests shuts the server down before exit.
        self.teardown = shutdown_server

class TestTiledVolume(object):

    @classmethod
    def setupClass(cls):
        cls.data_setup = DataSetup()
        cls.data_setup.setup()

    @classmethod
    def teardownClass(cls):
        cls.data_setup.teardown()

    def testBasic(self):
        tiled_volume = TiledVolume( self.data_setup.VOLUME_DESCRIPTION_FILE )
        tiled_volume.TEST_MODE = True
        roi = numpy.array( [(10, 150, 100), (30, 550, 550)] )
        result_out = numpy.zeros( roi[1] - roi[0], dtype=tiled_volume.description.dtype )
        tiled_volume.read( roi, result_out )
         
        ref_path_comp = PathComponents(self.data_setup.REFERENCE_VOL_PATH)
        with h5py.File(ref_path_comp.externalPath, 'r') as f:
            ref_data = f[ref_path_comp.internalPath][:]
 
        expected = ref_data[roiToSlice(*roi)]
         
        #numpy.save('/tmp/expected.npy', expected)
        #numpy.save('/tmp/result_out.npy', result_out)
 
        assert (expected == result_out).all()
 
    def testMissingTiles(self):
        tiled_volume = TiledVolume( self.data_setup.VOLUME_DESCRIPTION_FILE )
        tiled_volume.TEST_MODE = True
        # The test data should be missing slice 2, and the config doesn't remap the data.
        roi = numpy.array( [(0, 150, 100), (10, 550, 550)] )
        result_out = numpy.zeros( roi[1] - roi[0], dtype=tiled_volume.description.dtype )
        tiled_volume.read( roi, result_out )
            
        ref_path_comp = PathComponents(self.data_setup.REFERENCE_VOL_PATH)
        with h5py.File(ref_path_comp.externalPath, 'r') as f:
            ref_data = f[ref_path_comp.internalPath][:]
    
        # Slice 2 is missing from the server data
        expected = ref_data[roiToSlice(*roi)]
        expected[2] = 0
            
        #numpy.save('/tmp/expected.npy', expected)
        #numpy.save('/tmp/result_out.npy', result_out)
    
        assert (expected == result_out).all()
   
    def testRemappedTiles(self):
        # The config above specifies that slices 45:47 get their data from slice 44, 
        #  and slice 41 is the same as 40
        tiled_volume = TiledVolume( self.data_setup.VOLUME_DESCRIPTION_FILE )
        tiled_volume.TEST_MODE = True
        roi = numpy.array( [(40, 150, 100), (50, 550, 550)] )
        result_out = numpy.zeros( roi[1] - roi[0], dtype=tiled_volume.description.dtype )
        tiled_volume.read( roi, result_out )
           
        ref_path_comp = PathComponents(self.data_setup.REFERENCE_VOL_PATH)
        with h5py.File(ref_path_comp.externalPath, 'r') as f:
            ref_data = f[ref_path_comp.internalPath][:]
   
        # Slices 5,6,7 are missing from the server data, and 'filled in' with slice 4
        # Similarly, slice 1 is missing and filled in with slice 0.
        expected = ref_data[roiToSlice(*roi)]
        expected[5:8] = expected[4]
        expected[1] = expected[0]
           
        #numpy.save('/tmp/expected.npy', expected)
        #numpy.save('/tmp/result_out.npy', result_out)
   
        assert (expected == result_out).all()

class TestCustomAxes(object):

    @classmethod
    def setupClass(cls):
        cls.data_setup = DataSetup()
        cls.data_setup.setup()

    @classmethod
    def teardownClass(cls):
        cls.data_setup.teardown()
        
    def testCustomAxes(self):
        tiled_volume = TiledVolume( self.data_setup.TRANSPOSED_VOLUME_DESCRIPTION_FILE )
        tiled_volume.TEST_MODE = True
        roi = numpy.array( [(10, 150, 100), (30, 550, 550)] )
        result_out = numpy.zeros( roi[1] - roi[0], dtype=tiled_volume.description.dtype )

        roi_t = (tuple(reversed(roi[0])), tuple(reversed(roi[1])))
        result_out_t = result_out.transpose()
        
        tiled_volume.read( roi_t, result_out_t )
         
        ref_path_comp = PathComponents(self.data_setup.REFERENCE_VOL_PATH)
        with h5py.File(ref_path_comp.externalPath, 'r') as f:
            ref_data = f[ref_path_comp.internalPath][:]
 
        expected = ref_data[roiToSlice(*roi)]
         
        #numpy.save('/tmp/expected.npy', expected)
        #numpy.save('/tmp/result_out.npy', result_out)
 
        assert (expected == result_out).all()

if __name__ == "__main__":
    # Logging is OFF by default when running from command-line nose, i.e.:
    # nosetests thisFile.py)
    # When running this file directly, use the logging configuration below.    

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter( logging.Formatter("%(levelname)s %(name)s: %(message)s") )

    # Logging for this test
    logger.addHandler( handler )
    logger.setLevel( logging.INFO )
    ENABLE_SERVER_LOGGING = False

    # requests module logging
    requests_logger = logging.getLogger('requests')
    requests_logger.addHandler( handler )
    requests_logger.setLevel( logging.WARN )

    # tiledVolume logging
    tiledVolumeLogger = logging.getLogger("lazyflow.utility.io.tiledVolume")
    tiledVolumeLogger.addHandler( handler )
    tiledVolumeLogger.setLevel( logging.ERROR )

    import sys
    import nose
    sys.argv.append("--nocapture")    # Don't steal stdout.  Show it on the console as usual.
    sys.argv.append("--nologcapture") # Don't set the logging level to DEBUG.  Leave it alone.
    ret = nose.run(defaultTest=__file__)
    if not ret: sys.exit(1)
