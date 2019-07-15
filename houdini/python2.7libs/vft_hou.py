"""
-----------------------------------------------------------------------------
This source file has been developed within the scope of the
Technical Director course at Filmakademie Baden-Wuerttemberg.
http://technicaldirector.de
    
Written by Juraj Tomori.
Copyright (c) 2019 Animationsinstitut of Filmakademie Baden-Wuerttemberg
-----------------------------------------------------------------------------
"""

import hou
import toolutils
import os
import logging
import time

"""
todo
    * pass parameters as attributes, to reduce overhead of kernel re-compilation
    * pass in arbitrary combination of hybrids, primitives and boolean-combine them
"""

# logging config
logging.basicConfig(level=logging.DEBUG) # set to logging.INFO to disable DEBUG logs
log = logging.getLogger(__name__)

class NodeUtils(object):
    """
    set of methods operating on Houdini nodes
    """
    @staticmethod
    def getInputFractalNodes(root):
        """
        returns list of fractal nodes that are connected (upstream) to root node
        """
        # node type names of all fractal nodes
        fractals_nodes = set( ["vft_bristorbrotIter", "vft_mandelbulbPower2Iter", "vft_mengerSpongeIter"] )

        all_input_nodes = root.inputAncestors()
        input_fractal_nodes = []

        # find fractal nodes in all input nodes
        for node in all_input_nodes:
            if node.type().name() in fractals_nodes:
                input_fractal_nodes.append(node)

        return input_fractal_nodes

    @staticmethod
    def getOutputNodeByTypeName(start_node, type_name=""):
        """
        returns a connected node (downstream) which belongs to "vft generator" list
        """
        all_children_nodes = NodeUtils.outputChildren(start_node)
        out = None

        for node in all_children_nodes:
            if node.type().name() == type_name:
                out = node
                break
        
        return out

    @staticmethod
    def outputChildren(node):
        """
        find all descending connected (downstream) nodes
        """
        children = list( node.outputs() )
        for node in children:
            new_children = node.outputs()
            if len(new_children) == 0:
                break
            else:
                for child in new_children:
                    children.append( child )
                    NodeUtils.outputChildren(child)
        
        return children
    
    @staticmethod
    def fillKernelCodePythonSop():
        """
        this func will do all the parsing and will set up the kernel parm in descendant opencl node
        """
        start_time = time.time()    
        me = hou.pwd()
        geo = me.geometry()
        kernels_parm = me.parm("vft_kernels")

        # find a opencl downstream node
        cl_node = NodeUtils.getOutputNodeByTypeName(me, "opencl")

        # init a GenerateKernel object and init member var vft_kernels
        kernel = GenerateKernel()
        kernel.loadKernelsFileFromParm(kernels_parm)

        # get set of incoming fractals
        detail_attribs = geo.globalAttribs()

        # do the parsing
        kernel.parseKernelsFile(detail_attribs)

        # set vft_kernels_parsed to kernelcode parm in an opencl node if has changed
        cl_node_parm = cl_node.parm("kernelcode")
        old_cl_code = cl_node_parm.eval()

        if old_cl_code != kernel.vft_kernels_parsed:
            cl_node_parm.set(kernel.vft_kernels_parsed)
            log.debug("Kernel in OpenCL node updated")
        else:
            log.debug("Kernel in OpenCL is up to date")

        log.debug("Python SOP evaluated in {0:.8f} seconds \n\n".format( time.time() - start_time ))

class GenerateKernel(object):
    """
    class that will generate fractal generation CL code that Houdini will read from a string parameter and will execute
    """
    def __init__(self):
        self.vft_root_path = self.getVftRootFromPath( hou.getenv("HOUDINI_PATH") )
        self.vft_kernels_path = os.path.join(self.vft_root_path, "ocl", "vft_kernels.cl")

        self.vft_kernels = None
        self.vft_kernels_parsed = None

    def clStatementsToString(self, statements):
        """
        helper func
        """
        return ";\n".join(statements) + ";\n"
    
    def getVftRootFromPath(self, path):
        """
        this might not work on Windows
        extracts path to VFT from os-style paths string
        """
        
        paths = None
        # This is only for windows
        if os.name == 'nt':
            paths = path.split(";")
            # It's worth noting here that even if the paths in the houdini.env file have \ on Windows, they will get converted to / when using hou.getenv("HOUDINI_PATH"). Let's force convert them to \
            paths = [path.replace('/', '\\') for path in paths]

        else: # This is for non-windows OS
            paths = path.replace(";",":").split(":")

        # this will need to be changed if git repository name changes
        patterns = []
        patterns.append(os.sep + "vft-master" + os.sep + "houdini")
        patterns.append(os.sep + "vft" + os.sep + "houdini")

        # find pattern in list of paths
        vft_root = ""
        for path in paths:
            for pattern in patterns:
                if pattern in path:
                    vft_root = path
                    break
        
        return vft_root
    
    def loadKernelsFileToMemberVar(self):
        """
        loads vft_kernels.cl file into member variable
        """
        start_time = time.time()
        with open(self.vft_kernels_path, 'r') as file:
            self.vft_kernels = file.read()


        log.debug("Kernels file loaded from disk in {0:.8f} seconds".format( time.time() - start_time ))
    
    def loadKernelsFileToParm(self, parm):
        """
        loads vft_kernels.cl into specified parm object (which should be string) - this function should be called by a button for (re)loading a parm
        """
        if self.vft_kernels == None:
            self.loadKernelsFileToMemberVar()

        parm.set(self.vft_kernels)
    
    def loadKernelsFileFromParm(self, parm):
        """
        loads vft_kernels.cl into member var - either from disk, or parm (if it is loaded there already)
        """
        if parm.eval() == "":
            log.debug("Loading member var from file")
            self.loadKernelsFileToMemberVar()
        else:
            log.debug("Loading member var from node parameter")
            self.vft_kernels = parm.eval()
    
    def parseKernelsFile(self, attribs):
        """
        parses vft_kernels.cl file and replaces PY_* macros and saves it into member varible
        """
        start_time = time.time()
        self.vft_kernels_parsed = self.vft_kernels

        attribs_dict = {}
        for attr in attribs:
            attribs_dict[ attr.name() ] = attr.strings()

        # generate fractal stack
        fractal_stack_token = "#define PY_FRACTAL_STACK"

        fractal_stack_cl_code = ""
        try:
            fractal_stack_cl_code = self.clStatementsToString( self.generateClFractalStack(attribs_dict["fractal_stack"]) )
        except KeyError:
            pass
            log.error('No "fractal_stack" attribute found, probably missing input')

        fractal_stack_cl_code = fractal_stack_token + "\n\n" + fractal_stack_cl_code

        # generate pre-transform stack
        allowed_pre_transforms = ["curlNoise", "translateIter", "tgladFoldIter", "fabsFoldIter", "boxFoldIter", "rotationIter", "preScaleIter"]
        pre_transform_stack_token = "#define PY_PRE_TRANSFORM_STACK"

        pre_transform_stack_cl_code = ""
        try:
            pre_transform_stack_cl_code = self.clStatementsToString( self.generateClFractalStack(attribs_dict["pre-transform_stack"], valid_list=allowed_pre_transforms) )
        except KeyError:
            pass
            log.debug('No "pre-transform_stack" attribute found, probably missing input')

        pre_transform_stack_cl_code = pre_transform_stack_token + "\n\n" + pre_transform_stack_cl_code


        self.vft_kernels_parsed = self.vft_kernels_parsed.replace(fractal_stack_token, fractal_stack_cl_code)
        self.vft_kernels_parsed = self.vft_kernels_parsed.replace(pre_transform_stack_token, pre_transform_stack_cl_code)

        log.debug("Kernels file parsed in {0:.8f} seconds".format( time.time() - start_time ))
    
    def generateClFractalStack(self, fractal_attribs, valid_list=None):
        """
        returns a list of CL statements with fractal function calls from a list of fractal nodes
        """
       
        # a dictionary mapping strings of arguments to OpenCL fractal function names
        args_dict = {
            "default" :  "{obj.cl_function_name}(Z, de, P_in, log_lin, {obj.parms[weight]:.6f}f, (float4)({obj.parms[julia_mode]:.1f}f, {obj.parms[juliax]:.6f}f, {obj.parms[juliay]:.6f}f, {obj.parms[juliaz]:.6f}f))",
            "mandelboxIter" : "{obj.cl_function_name}(Z, de, P_in, log_lin, {obj.parms[weight]:.6f}f, (float4)({obj.parms[julia_mode]:.1f}f, {obj.parms[juliax]:.6f}f, {obj.parms[juliay]:.6f}f, {obj.parms[juliaz]:.6f}f), {obj.parms[scale]:.6f}f)",
            "mengerSpongeIter" : "{obj.cl_function_name}(Z, de, P_in, log_lin, {obj.parms[weight]:.6f}f, (float4)({obj.parms[julia_mode]:.1f}f, {obj.parms[juliax]:.6f}f, {obj.parms[juliay]:.6f}f, {obj.parms[juliaz]:.6f}f), {obj.parms[modulus]:.0f})",
            "mandelbulbIter" : "{obj.cl_function_name}(Z, de, P_in, log_lin, {obj.parms[weight]:.6f}f, (float4)({obj.parms[julia_mode]:.1f}f, {obj.parms[juliax]:.6f}f, {obj.parms[juliay]:.6f}f, {obj.parms[juliaz]:.6f}f), {obj.parms[power]:.6f}f)",
            "xenodreambuieIter" : "{obj.cl_function_name}(Z, de, P_in, log_lin, {obj.parms[weight]:.6f}f, (float4)({obj.parms[julia_mode]:.1f}f, {obj.parms[juliax]:.6f}f, {obj.parms[juliay]:.6f}f, {obj.parms[juliaz]:.6f}f), {obj.parms[power]:.6f}f, {obj.parms[alpha]:.6f}f, {obj.parms[beta]:.6f}f)",
            "sierpinski3dIter" : "{obj.cl_function_name}(Z, de, P_in, log_lin, {obj.parms[weight]:.6f}f, (float4)({obj.parms[julia_mode]:.1f}f, {obj.parms[juliax]:.6f}f, {obj.parms[juliay]:.6f}f, {obj.parms[juliaz]:.6f}f), {obj.parms[scale]:.6f}f, (float3)({obj.parms[offsetx]:.6f}f, {obj.parms[offsety]:.6f}f, {obj.parms[offsetz]:.6f}f), (float3)({obj.parms[rotx]:.6f}f, {obj.parms[roty]:.6f}f, {obj.parms[rotz]:.6f}f))",
            "mengerSmoothIter" : "{obj.cl_function_name}(Z, de, P_in, log_lin, {obj.parms[weight]:.6f}f, (float4)({obj.parms[julia_mode]:.1f}f, {obj.parms[juliax]:.6f}f, {obj.parms[juliay]:.6f}f, {obj.parms[juliaz]:.6f}f), {obj.parms[scale]:.6f}f, {obj.parms[offset_s]:.6f}f, (float3)({obj.parms[offset_cx]:.6f}f, {obj.parms[offset_cy]:.6f}f, {obj.parms[offset_cz]:.6f}f), (float3)({obj.parms[rotx]:.6f}f, {obj.parms[roty]:.6f}f, {obj.parms[rotz]:.6f}f))",
            "amazingSurfIter" : "{obj.cl_function_name}(Z, de, P_in, log_lin, {obj.parms[weight]:.6f}f, (float4)({obj.parms[julia_mode]:.1f}f, {obj.parms[juliax]:.6f}f, {obj.parms[juliay]:.6f}f, {obj.parms[juliaz]:.6f}f), {obj.parms[foldx]:.6f}f, {obj.parms[foldy]:.6f}f, {obj.parms[force_cylindrical_fold]:.0f}, {obj.parms[min_radius]:.6f}f, {obj.parms[scale]:.6f}f, {obj.parms[scale_fold_influence]:.6f}f, (float3)({obj.parms[rotx]:.6f}f, {obj.parms[roty]:.6f}f, {obj.parms[rotz]:.6f}f), {obj.parms[multiply_c]:.0f}, (float3)({obj.parms[c_multiplierx]:.6f}f, {obj.parms[c_multipliery]:.6f}f, {obj.parms[c_multiplierz]:.6f}f))",
            "mandelbulb4Iter" : "{obj.cl_function_name}(Z, de, P_in, log_lin, {obj.parms[weight]:.6f}f, (float4)({obj.parms[julia_mode]:.1f}f, {obj.parms[juliax]:.6f}f, {obj.parms[juliay]:.6f}f, {obj.parms[juliaz]:.6f}f), {obj.parms[power]:.6f}f, (float3)({obj.parms[anglesx]:.6f}f, {obj.parms[anglesy]:.6f}f, {obj.parms[anglesz]:.6f}f))",
            "idesIter" : "{obj.cl_function_name}(Z, de, P_in, log_lin, {obj.parms[weight]:.6f}f, (float4)({obj.parms[julia_mode]:.1f}f, {obj.parms[juliax]:.6f}f, {obj.parms[juliay]:.6f}f, {obj.parms[juliaz]:.6f}f), (float3)({obj.parms[multiplierx]:.6f}f, {obj.parms[multipliery]:.6f}f, {obj.parms[multiplierz]:.6f}f), (float2)({obj.parms[sub_multiplierx]:.6f}f, {obj.parms[sub_multipliery]:.6f}f))",
            "ides2Iter" : "{obj.cl_function_name}(Z, de, P_in, log_lin, {obj.parms[weight]:.6f}f, (float4)({obj.parms[julia_mode]:.1f}f, {obj.parms[juliax]:.6f}f, {obj.parms[juliay]:.6f}f, {obj.parms[juliaz]:.6f}f), (float3)({obj.parms[multiplierx]:.6f}f, {obj.parms[multipliery]:.6f}f, {obj.parms[multiplierz]:.6f}f), (float2)({obj.parms[sub_multiplierx]:.6f}f, {obj.parms[sub_multipliery]:.6f}f))",
            "iqBulbIter" : "{obj.cl_function_name}(Z, de, P_in, log_lin, {obj.parms[weight]:.6f}f, (float4)({obj.parms[julia_mode]:.1f}f, {obj.parms[juliax]:.6f}f, {obj.parms[juliay]:.6f}f, {obj.parms[juliaz]:.6f}f), {obj.parms[power]:.6f}f, {obj.parms[zpower]:.6f}f)",
            "quaternion3dIter" : "{obj.cl_function_name}(Z, de, P_in, log_lin, {obj.parms[weight]:.6f}f, (float4)({obj.parms[julia_mode]:.1f}f, {obj.parms[juliax]:.6f}f, {obj.parms[juliay]:.6f}f, {obj.parms[juliaz]:.6f}f), (float3)({obj.parms[scalex]:.6f}f, {obj.parms[scaley]:.6f}f, {obj.parms[scalez]:.6f}f), (float3)({obj.parms[offsetx]:.6f}f, {obj.parms[offsety]:.6f}f, {obj.parms[offsetz]:.6f}f), (float3)({obj.parms[rotx]:.6f}f, {obj.parms[roty]:.6f}f, {obj.parms[rotz]:.6f}f), {obj.parms[de_influence]:.6f}f)",
            "josKleinianIter" : "{obj.cl_function_name}(Z, de, P_in, log_lin, {obj.parms[weight]:.6f}f, (float4)({obj.parms[julia_mode]:.1f}f, {obj.parms[juliax]:.6f}f, {obj.parms[juliay]:.6f}f, {obj.parms[juliaz]:.6f}f), {obj.parms[r]:.6f}f, {obj.parms[l]:.6f}f, (float3)({obj.parms[box_sizex]:.6f}f, {obj.parms[box_sizey]:.6f}f, {obj.parms[box_sizez]:.6f}f))",
            "rotationIter" :  "{obj.cl_function_name}(Z, (float3)({obj.parms[rotx]:.6f}f, {obj.parms[roty]:.6f}f, {obj.parms[rotz]:.6f}f))",
            "boxFoldIter" :  "{obj.cl_function_name}(Z, (int3)({obj.parms[axis_enablex]:.0f}, {obj.parms[axis_enabley]:.0f}, {obj.parms[axis_enablez]:.0f}), {obj.parms[folding_limit]:.6f}f, {obj.parms[folding_value]:.6f}f, {obj.parms[z_scale]:.6f}f)",
            "fabsFoldIter" :  "{obj.cl_function_name}(Z, (int3)({obj.parms[axis_enablex]:.0f}, {obj.parms[axis_enabley]:.0f}, {obj.parms[axis_enablez]:.0f}), (float3)({obj.parms[offsetx]:.6f}f, {obj.parms[offsety]:.6f}f, {obj.parms[offsetz]:.6f}f))",
            "tgladFoldIter" :  "{obj.cl_function_name}(Z, (int3)({obj.parms[axis_enablex]:.0f}, {obj.parms[axis_enabley]:.0f}, {obj.parms[axis_enablez]:.0f}), (float3)({obj.parms[offsetx]:.6f}f, {obj.parms[offsety]:.6f}f, {obj.parms[offsetz]:.6f}f))",
            "scaleIter" :  "{obj.cl_function_name}(Z, de, (float3)({obj.parms[scalex]:.6f}f, {obj.parms[scaley]:.6f}f, {obj.parms[scalez]:.6f}f))",
            "translateIter" :  "{obj.cl_function_name}(Z, (float3)({obj.parms[translatex]:.6f}f, {obj.parms[translatey]:.6f}f, {obj.parms[translatez]:.6f}f))",
            "addCOffsetIter" :  "{obj.cl_function_name}(Z, P_in, (float3)({obj.parms[offsetx]:.6f}f, {obj.parms[offsety]:.6f}f, {obj.parms[offsetz]:.6f}f))",
            "curlNoise" :  "{obj.cl_function_name}(theXNoise, Z, (float4)({obj.parms[frequencyx]:.6f}f, {obj.parms[frequencyy]:.6f}f, {obj.parms[frequencyz]:.6f}f, {obj.parms[frequencyw]:.6f}f), (float4)({obj.parms[offsetx]:.6f}f, {obj.parms[offsety]:.6f}f, {obj.parms[offsetz]:.6f}f, {obj.parms[offsetw]:.6f}f), (float3)({obj.parms[amplitudex]:.6f}f, {obj.parms[amplitudey]:.6f}f, {obj.parms[amplitudez]:.6f}f))",
            "preScaleIter" :  "{obj.cl_function_name}(Z, (float3)({obj.parms[scalex]:.6f}f, {obj.parms[scaley]:.6f}f, {obj.parms[scalez]:.6f}f))"
        }

        def args_format(args_dict, obj):
            """
            if a function has some custom arguments, then their formatting is specified here
            """
            if obj.cl_function_name in args_dict:

                # this line is picking a string to be formatted from args_dict dictionary
                string = args_dict[obj.cl_function_name]
                string = string.format( obj=obj )

            # if function has not arguments mapping in args_dict, then it is considered to use default one
            else:
                string = args_dict["default"]
                string = string.format( obj=obj )
            
            return string

        fractal_objects = []
        for attrib in fractal_attribs:
            obj = FractalObject()
            obj.attribToVars(attrib)

            if valid_list:
                if obj.cl_function_name in valid_list:
                    fractal_objects.append(obj)
            else:
                fractal_objects.append(obj)

        # list which will hold CL fractal funcs calls
        stack = []

        for obj in fractal_objects:
            statement = args_format(args_dict, obj)
            log.debug(statement)
            stack.append(statement)

        return stack

class FractalObject(object):
    """
    a class that will hold data and functionality for getting fractal data from detail attribute
    """
    def __init__(self):
        # init member vars
        self.asset_name = None
        self.parent_name = None
        self.parent_path = None
        self.cl_function_name = None
        self.parms = {
            "weight" : 1.0,
            "julia_mode" : 0,
            "juliax" : 0.0,
            "juliay" : 0.0,
            "juliaz" : 0.0
        }
    
    def attribToVars(self, attrib):
        """
        this will parse attribute string and will set member vars from it
        """
        attrib_list = attrib.split("|")

        self.asset_name = attrib_list[0]
        self.parent_name = attrib_list[1]
        self.parent_path = attrib_list[2]

        self.cl_function_name = self.asset_name.split("_")[-1]

        parms_string = attrib_list[3]
        parms_list = parms_string.split(",")

        for item in parms_list:
            item_split = item.split(":")
            self.parms[ item_split[0] ] = float(item_split[1])

    def nodeToVars(self):
        """
        this will fill in member vars based on a node, it should be used inside of a fractal node
        """
        node = hou.pwd()

        self.asset_name = node.parent().type().name()
        self.parent_name = node.parent().name()
        self.parent_path = node.parent().path()

        parms = node.parent().parms()
        for parm in parms:
            if parm.parmTemplate().type() != hou.parmTemplateType.Label:
                self.parms[ parm.name() ] = parm.eval()
    
    def varsToAttrib(self):
        """
        this will serialize member vars into a string, which should be stored in detail attribute
        """
        parms_string = ""

        for key, value in self.parms.iteritems():
            parms_string += key + ":" + "{0:.6f}".format(value) + ","
        parms_string = parms_string[:-1] # remove the last comma
        
        attrib = "|".join( [self.asset_name, self.parent_name, self.parent_path, parms_string] )
        return attrib

class FpsCam(object):
    """
    Set of functions helping with FPS/Flying camera orientation in scenes.
    """
    pass
