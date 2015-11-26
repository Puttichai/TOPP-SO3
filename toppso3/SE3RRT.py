#RRT implementation for reorientation with collision-free
from openravepy import *
from pylab import *

import time
import string
import numpy as np
import copy
import random
import os
import Heap

import lie as Lie
import Utils as SE3Utils
from Utils import Colorize
import TOPP

# global variables for RRTPlanners
FW = 0
BW = 1
REACHED = 0
ADVANCED = 1
TRAPPED = 2

# gobal variables for collision checking 
INCOLLISION = -1
OK = 1

class Config():
    """Attributes:
         q   -- quaternion vector
         qs  -- angular velocity

         qt  -- translation vector
         qts -- translational velocity
    """
    def __init__(self, q, qt, qs=None, qts=None, qss=None, qtss=None):
        self.q = q
        if qs is None:
            self.qs = zeros(3)
        else:
            self.qs = qs

        self.qt = qt
        if qts is None:
            self.qts = zeros(3)
        else:
            self.qts = qts


class Vertex():
    """Attributes:
         config     -- stores a Config obj
         parent     -- the parent for FW vertex, the child for BW vertex
         trajstring -- a trajectory from its parent (or child)
         level      -- its level from the root of the tree (0 for the root)
    """
    def __init__(self, config, vertextype = FW):
        self.config = config
        self.vertextype = vertextype
        self.parent = None # to be assigned when added to a tree
        self.traj = '' # to be assigned when added to a tree (rot)
        self.trajtran = '' # to be assigned when added to a tree (trans)
        self.level = 0


class Tree():
    """Attributes:
         verticeslist -- stores all vertices added to the tree
         treetype     -- FW or BW    
    """
    def __init__(self, treetype=FW, vroot=None):
        if vroot is None:
            self.verticeslist = []
        else:
            self.verticeslist = [vroot]
        self.treetype = treetype

        
    def __len__(self):
        return len(self.verticeslist)

    
    def __getitem__(self, index):
        return self.verticeslist[index]        
    
    
    def AddVertex(self, parent, traj, trajtran, vnew):
        vnew.parent = parent
        vnew.traj = traj
        vnew.trajtran = trajtran
        self.verticeslist.append(vnew)

        
    def GenTrajList(self):
        trajlist = []
        if (self.treetype == FW):
            vertex = self.verticeslist[-1]
            parent = vertex.parent
            while (vertex.parent is not None):
                trajlist.append(vertex.traj)
                vertex = parent
                if (vertex.parent is not None):
                    parent = vertex.parent
            trajlist = trajlist[::-1]
        else:
            vertex = self.verticeslist[-1]
            while (vertex.parent is not None):
                trajlist.append(vertex.traj)
                if (vertex.parent is not None):
                    vertex = vertex.parent
        return trajlist
    
    
    def GenRotationMatList(self):
        RotationMatList = []
        if (self.treetype == FW):
            vertex = self.verticeslist[-1]
            RotationMatList.append(rotationMatrixFromQuat(vertex.config.q))
            parent = vertex.parent
            while (vertex.parent is not None):
                RotationMatList.append(rotationMatrixFromQuat(parent.config.q))
                vertex = parent
                if (vertex.parent is not None):
                    parent = vertex.parent
            RotationMatList =  RotationMatList[::-1]
        else:
            vertex = self.verticeslist[-1]
            RotationMatList.append(rotationMatrixFromQuat(vertex.config.q))
            while (vertex.parent is not None):
                RotationMatList.append(rotationMatrixFromQuat(vertex.parent.config.q))
                if (vertex.parent is not None):
                    vertex = vertex.parent
        return RotationMatList

    
    def GenTrajTranString(self):
        trajtranlist = []
        if (self.treetype == FW):
            vertex = self.verticeslist[-1]
            parent = vertex.parent
            while (vertex.parent is not None):
                trajtranlist.append(vertex.trajtran)
                vertex = parent
                if (vertex.parent is not None):
                    parent = vertex.parent
            trajtranlist = trajtranlist[::-1]
        else:
            vertex = self.verticeslist[-1]
            while (vertex.parent is not None):
                trajtranlist.append(vertex.trajtran)
                if (vertex.parent is not None):
                    vertex = vertex.parent
        trajectorytranstring = ''
        for i in range(len(trajtranlist)):
            trajectorytranstring += "\n"
            trajectorytranstring += trajtranlist[i]
        trajectorytranstring = string.lstrip(trajectorytranstring) # remove leading "\n"
        return trajectorytranstring

    
class RRTPlanner():
    """Base class for RRT planners"""
    REACHED = 0
    ADVANCED = 1
    TRAPPED = 2

    def __init__(self, vertex_start, vertex_goal, robot):
        """Initialize a planner. RRTPlanner always has two trees. For
        a unidirectional planner, the treeend will not be extended and
        always has only one vertex, vertex_goal.
        """        
        # np.random.seed(np.random.randint(0, 10))
        ## need more unpredictable sequence than that generated from np.random
        self._RNG = random.SystemRandom()
        self.robot = robot
        self.treestart = Tree(FW, vertex_start)
        self.treeend = Tree(BW, vertex_goal)
        self.connectingtraj = []
        self.connectingtrajtran = []
        self.runningtime = 0.0
        self.nn = -1
        self.iterations = 0
        self.result = False
        
        # DEFAULT PARAMETERS  
        self.STEPSIZE = 0.7
        self.INTERPOLATIONDURATION = 0.5
        self.PRINT = True
        self.discrtimestep = 1e-2 ## for collision checking, etc.
        self._settranslationallimits = False
        
        
    def SetTranslationalLimits(self, upper, lower=[]):
        self.uppertlimits = upper
        if len(lower) == 0:
            self.lowertlimits = -1.0*self.uppertlimits
        else:
            self.lowertlimits = lower
        if self.lowertlimits[2] < 0:
            self.lowertlimits[2] = 0
        self._settranslationallimits = True


    def __str__(self):
        ret = "Total running time :" + str(self.runningtime) + "sec.\n"
        ret += "Total number of iterations :" + str(self.iterations)
        return ret

    
    def RandomConfig(self):
        """RandomConfig samples a random configuration uniformly from
        the quaternion unit sphere in four dimensions.
        """        
        q_rand = Lie.RandomQuat()
        qs_rand = np.array([1e-3, 1e-3, 1e-3])
        
        qt_rand = []
        for i in xrange(3):
            qt_rand.append(self._RNG.uniform(self.lowertlimits[i], 
                                             self.uppertlimits[i]))
        qts_rand = np.zeros(3)

        return Config(q_rand, qt_rand, qs_rand, qts_rand)
    

    def Extend(self, c_rand):
        if (np.mod(self.iterations - 1, 2) == FW):
            ## treestart is to be extended
            res = self.ExtendFW(c_rand)
        else:
            ## treeend is to be extended
            res = self.ExtendBW(c_rand)
        return res


    def ExtendFW(self, c_rand):
        nnindices = self.NearestNeighborIndices(c_rand, FW)
        for index in nnindices:
            v_near = self.treestart.verticeslist[index]
            q_beg = v_near.config.q
            qs_beg = v_near.config.qs
            
            qt_beg = v_near.config.qt
            qts_beg = v_near.config.qts

            ## check if c_rand is too far from vnear
            delta = self.Distance(v_near.config, c_rand)
            if (delta <= self.STEPSIZE):
                q_end = c_rand.q
                STATUS = REACHED
            else:
                q_end = q_beg + self.STEPSIZE*(c_rand.q - q_beg)/np.sqrt(delta)
                q_end /= np.linalg.norm(q_end)
                STATUS = ADVANCED
            qs_end = c_rand.qs

            qt_end = c_rand.qt
            qts_end = c_rand.qts
            c_new = Config(q_end, qt_end, qs_end, qts_end)

            ## check feasibility of c_new
            if (not self.IsFeasibleConfig(c_new)):
                if self.PRINT:
                    print '    [ExtendFW] TRAPPED (infeasible configuration)'
                STATUS = TRAPPED
                continue            
            
            ## interpolate a trajectory
            trajectory = Lie.InterpolateSO3(rotationMatrixFromQuat(q_beg),
                                            rotationMatrixFromQuat(q_end),
                                            qs_beg, qs_end, self.INTERPOLATIONDURATION)
            trajectorytranstring = SE3Utils.TrajString3rdDegree\
            (qt_beg, qt_end, qts_beg, qts_end, self.INTERPOLATIONDURATION)
            
            ## check feasibility ( collision checking for the trajectory)
            result = self.IsFeasibleTrajectory(trajectory, trajectorytranstring, 
                                               q_beg, qt_beg, FW) 
            if (result[0] == OK):
                  ## extension is now successful
                v_new = Vertex(c_new, FW)
                v_new.level = v_near.level + 1
                self.treestart.AddVertex(v_near, trajectory, trajectorytranstring, v_new)
                return STATUS
            else:
                if self.PRINT:
                    print '    [ExtendFW] TRAPPED (trajectory in collision)'
                STATUS = TRAPPED
        return STATUS
    

    def ExtendBW(self, c_rand):
        nnindices = self.NearestNeighborIndices(c_rand, BW)
        for index in nnindices:
            v_near = self.treeend.verticeslist[index]
            q_end = v_near.config.q
            qs_end = v_near.config.qs

            qt_end = v_near.config.qt
            qts_end = v_near.config.qts
            ## check if c_rand is too far from vnear
            delta = self.Distance(v_near.config, c_rand)
            if (delta <= self.STEPSIZE):
                q_beg = c_rand.q
                STATUS = REACHED
            else:
                q_beg = q_end + self.STEPSIZE*(c_rand.q - q_end)/np.sqrt(delta)
                q_beg /= np.linalg.norm(q_beg)
                STATUS = ADVANCED
            qs_beg = c_rand.qs

            qt_beg = c_rand.qt
            qts_beg = c_rand.qts
            c_new = Config(q_beg, qt_beg, qs_beg, qts_beg)
            
            ## check feasibility of c_new
            if (not self.IsFeasibleConfig(c_new)):
                if self.PRINT:
                    print '    [ExtendBW] TRAPPED (infeasible configuration)'
                STATUS = TRAPPED
                continue            

            ## interpolate a trajectory
            trajectory = Lie.InterpolateSO3(rotationMatrixFromQuat(q_beg),
                                            rotationMatrixFromQuat(q_end),
                                            qs_beg, qs_end, self.INTERPOLATIONDURATION)

            trajectorytranstring = SE3Utils.TrajString3rdDegree\
            (qt_beg, qt_end, qts_beg, qts_end, self.INTERPOLATIONDURATION)
            
            ## check feasibility ( collision checking for the trajectory)
            result = self.IsFeasibleTrajectory(trajectory, trajectorytranstring, 
                                               q_beg, qt_beg, BW)
            if (result[0] == OK):
                ## extension is now successful
                v_new = Vertex(c_new, BW)
                v_new.level = v_near.level + 1
                self.treeend.AddVertex(v_near, trajectory,trajectorytranstring, v_new)
                return STATUS
            else:
                if self.PRINT:
                    print '    [ExtendBW] TRAPPED (trajectory in collision)'
                STATUS = TRAPPED
        return STATUS


    def Connect(self):
        if (np.mod(self.iterations - 1, 2) == FW):
            ## treestart has just been extended
            res = self.ConnectBW()
        else:
            ## treeend has just been extended
            res = self.ConnectFW()
        return res
        
    def ConnectFW(self):
        v_test = self.treeend.verticeslist[-1]
        nnindices = self.NearestNeighborIndices(v_test.config, FW)
        for index in nnindices:
            v_near = self.treestart.verticeslist[index]
            
            q_beg = v_near.config.q
            qs_beg = v_near.config.qs
            qt_beg = v_near.config.qt
            qts_beg = v_near.config.qts

            q_end = v_test.config.q
            qs_end = v_test.config.qs
            qt_end = v_test.config.qt
            qts_end = v_test.config.qts
            
            ## interpolate a trajectory
            trajectory = Lie.InterpolateSO3(rotationMatrixFromQuat(q_beg),
                                            rotationMatrixFromQuat(q_end),
                                            qs_beg, qs_end, self.INTERPOLATIONDURATION)
            trajectorytranstring = SE3Utils.TrajString3rdDegree\
            (qt_beg, qt_end, qts_beg, qts_end, self.INTERPOLATIONDURATION)
            
            ## check feasibility (collision checking for the trajectory)
            result = self.IsFeasibleTrajectory(trajectory, trajectorytranstring, 
                                               q_beg, qt_beg, FW)
            if (result[0] == 1):
                ## conection is now successful
                self.treestart.verticeslist.append(v_near)
                self.connectingtraj = trajectory
                self.connectingtrajtran = trajectorytranstring
                return REACHED
        return TRAPPED
    

    def ConnectBW(self):
        v_test = self.treestart.verticeslist[-1]
        nnindices = self.NearestNeighborIndices(v_test.config, BW)
        for index in nnindices:
            v_near = self.treeend.verticeslist[index]
            
            q_end = v_near.config.q
            qs_end = v_near.config.qs
            qt_end = v_near.config.qt
            qts_end = v_near.config.qts

            q_beg = v_test.config.q
            qs_beg = v_test.config.qs
            qt_beg = v_test.config.qt
            qts_beg = v_test.config.qts

            ## interpolate a trajectory
            trajectory = Lie.InterpolateSO3(rotationMatrixFromQuat(q_beg),
                                            rotationMatrixFromQuat(q_end),
                                            qs_beg, qs_end, self.INTERPOLATIONDURATION)
            trajectorytranstring = SE3Utils.TrajString3rdDegree\
            (qt_beg, qt_end, qts_beg, qts_end, self.INTERPOLATIONDURATION)
            
            ## check feasibility (collision checking for the trajectory)
            result = self.IsFeasibleTrajectory(trajectory, trajectorytranstring, 
                                               q_beg, qt_beg, BW)
            if (result[0] == 1):
                 ## conection is now successful
                self.treeend.verticeslist.append(v_near)
                self.connectingtraj = trajectory
                self.connectingtrajtran = trajectorytranstring
                return REACHED
        return TRAPPED
    

    def IsFeasibleConfig(self, c_rand):
        """IsFeasibleConfig checks feasibility of the given Config object. 
        Feasibility conditions are to be determined by each RRT planner.
        """
        env = self.robot.GetEnv()
        with self.robot:
            transformation = eye(4)
            transformation[0:3,0:3] = rotationMatrixFromQuat(c_rand.q)
            transformation[0:3,3] = c_rand.qt
            self.robot.SetTransform(transformation)
            isincollision = (env.CheckCollision(self.robot, CollisionReport()))
            if (isincollision):
                return False
            else:
                return True


    def IsFeasibleTrajectory(self, trajectory, trajectorytranstring, 
                             q_beg, qt_beg, direction):
        """IsFeasibleTrajectory checks feasibility of the given trajectory.
        Feasibility conditions are to be determined by each RRT planner.
        """
        ## check collision
        env = self.robot.GetEnv()
        traj = trajectory
        R_beg =  rotationMatrixFromQuat(q_beg)
        trajtran = TOPP.Trajectory.PiecewisePolynomialTrajectory.FromString\
        (trajectorytranstring)
        
        for s in np.arange(0, traj.duration, self.discrtimestep):
            with self.robot:
                transformation = eye(4)
                transformation[0:3,0:3] = Lie.EvalRotation(R_beg, traj, s)
                transformation[0:3,3] = trajtran.Eval(s)
 
                self.robot.SetTransform(transformation)
                isincollision = (env.CheckCollision(self.robot, CollisionReport()))
                
            if (isincollision):
                return [INCOLLISION]

        with self.robot:
            self.robot.SetTransform(transformation)
            isincollision = (env.CheckCollision(self.robot, CollisionReport()))
        if (isincollision):
            return [INCOLLISION]
        else:
            if (direction == FW):
                return [OK]
            else:
                return [OK]


    def Run(self, allottedtime):
        if (self.result):
            print "The planner has already found a path."
            return True

        t = 0.0
        prev_it = self.iterations
        it = 0

        t_begin = time.time()
        if (self.Connect() == REACHED):
            print Colorize('Path found', 'green')
            print Colorize('    Total number of iterations : {0}'.format\
                               (self.iterations), 'green')
            t_end = time.time()
            t += t_end - t_begin
            self.runningtime += t
            print Colorize('    Total running time : {0} sec.'.format\
                               (self.runningtime), 'green')
            self.result = True
            return self.result


        while (t < allottedtime):
            it += 1
            self.iterations += 1
            print Colorize('iteration : {0}'.format(it), 'blue')
            t_begin = time.time()
            
            c_rand = self.RandomConfig()
            if (self.Extend(c_rand) != TRAPPED):
                print Colorize('Tree start : {0}; Tree end : {1}'.\
                                   format(len(self.treestart.verticeslist), 
                                          len(self.treeend.verticeslist)),
                               'green')
                
                if (self.Connect() == REACHED):
                    print Colorize('Path found', 'green')
                    print Colorize('    Total number of iterations : {0}'.format\
                                       (self.iterations), 'green')
                    t_end = time.time()
                    t += t_end - t_begin
                    self.runningtime += t
                    print Colorize('    Total running time : {0} sec.'.format\
                                       (self.runningtime), 'green')
                    self.result = True
                    return self.result
                
            t_end = time.time()
            t += t_end - t_begin
            self.runningtime += t_end - t_begin
            
        print Colorize('Allotted time {0} sec. is exhausted after {1} iterations'.\
                           format(allottedtime, self.iterations - prev_it))
        
        return self.result


    def Distance(self, c_test0, c_test1):
        """Distance measures distance between 2 configs, ctest0 and ctest1
        """
        X0 = eye(4)
        X1 = eye(4)
        X0[:3,:3] = rotationMatrixFromQuat(c_test0.q)
        X0[:3,3] = c_test0.qt
        X1[:3,:3] = rotationMatrixFromQuat(c_test1.q)
        X1[:3,3] = c_test1.qt
        return SE3Utils.SE3Distance(X0, X1,1/pi, 1)

        
    def NearestNeighborIndices(self, c_rand, treetype, custom_nn = 0):
        """NearestNeighborIndices returns indices of self.nn nearest
        neighbors of c_rand on the tree specified by treetype.
        """
        if (treetype == FW):
            tree = self.treestart
            nv = len(tree)
        else:
            tree = self.treeend
            nv = len(tree)
            
        distancelist = [self.Distance(c_rand, v.config) for v in tree.verticeslist]
        distanceheap = Heap.Heap(distancelist)
        
        if (custom_nn == 0):
            nn = self.nn
        else:
            nn = custom_nn
        
        if (nn == -1): #using all of the vertexes in the tree
            nn = nv
        else:
            nn = min(self.nn, nv)
        nnindices = [distanceheap.ExtractMin()[0] for i in range(nn)]
        return nnindices


    def GenFinalTrajList(self):
        if (not self.result):
            print "The Planner did not find any path from start to goal."
            return []
        TrajectoryList = []
        TrajectoryList = self.treestart.GenTrajList()
        if (self.connectingtraj != []):
            TrajectoryList.append(self.connectingtraj)
        if (self.treeend.GenTrajList()!= []):
            TrajectoryList.extend(self.treeend.GenTrajList())
        #print len(TrajectoryList)
        return TrajectoryList

    
    def GenFinalRotationMatrixList(self):
        if (not self.result):
            print "The Planner did not find any path from start to goal."
            return []
        
        RotationMatrixList = []
        RotationMatrixList = self.treestart.GenRotationMatList()
        if (self.treeend.GenRotationMatList() != []):
            RotationMatrixList.extend(self.treeend.GenRotationMatList())
        RotationMatrixList.pop()
        #print len(RotationMatrixList)
        return RotationMatrixList

    def GenFinalTrajTranString(self):
        if (not self.result):
            print "The Planner did not find any path from start to goal."
            return ''
        
        trajectorytranstring = ''
        trajectorytranstring += self.treestart.GenTrajTranString()
        if not (self.connectingtrajtran == ''):
            if not (trajectorytranstring == ''):
                trajectorytranstring += "\n"
            trajectorytranstring += self.connectingtrajtran
        trajtranstring_treeend = self.treeend.GenTrajTranString()
        if (not trajtranstring_treeend == ''):
            trajectorytranstring += "\n"
            trajectorytranstring += trajtranstring_treeend
        
        return trajectorytranstring
