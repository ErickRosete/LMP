import numpy as np
import glob
import pickle
import matplotlib.pyplot as plt
from networks.play_lmp import PlayLMP
import torch
import cv2
import os
import gym
import sys
sys.path.append("./relay-policy-learning/adept_envs/")
import adept_envs
import utils.constants as constants
import skvideo.io
import argparse
from tqdm import tqdm
import keyboard
import time 

#Load actions from .pkl file. Use absolute path including extension name
def load_actions_data(file_name):
    path = {'actions': [], 'init_qpos':[], 'init_qvel':[]} #Only retrieve this keys
    if os.path.getsize(file_name) > 0:   #Check if the file is not empty
        with open(file_name, 'rb') as f:
            data = pickle.load(f)
            for key in path.keys():
                if key == "observations":
                    path[key] = data[key][:, :9]
                else:
                    path[key] = data[key]
    return path

#Print imgs from validation packages. Use i to select the nth package.
#n_packages to select how many packages to load (i.e, val_data[i:i+n_packages])
def print_img_goals(data_dir="./data/validation/", save_folder = "./data/goals/", i=0, n_packages=1, load_all = False):
    data_files = glob.glob(data_dir + "*.pkl")
    if not load_all:
        data_files = data_files[i:i+n_packages]
    print("Printing images...")
    print(data_files)

    data_img = []
    try:
        for i, file in enumerate(data_files):
            #load images of file
            with open(file, 'rb') as f:
                if(i==0):
                    data_img = pickle.load(f)['images']
                else:
                    data_img = np.concatenate(pickle.load(f)['images'], axis=0)
            if not os.path.exists(save_folder):
                os.makedirs(save_folder)
            for i,img in enumerate(data_img):
                save_path = save_folder + os.path.basename(file)[:-4] + "_img_" + str(i) + ".png"
                cv2.imwrite(save_path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR)) #save as blue shelfs
    except Exception as e:
        print(e)

    print("done!")

#init environment with pos and vel from given file
def init_env(env, file_name):
    if os.path.getsize(file_name) > 0:   #Check if the file is not empty
        with open(file_name, 'rb') as f:
            data = pickle.load(f)
            env.sim.data.qpos[:] = data['init_qpos'].copy()
            env.sim.data.qvel[:] = data['init_qvel'].copy()
            env.sim.forward()
    return env

#Proxy function to either save or render a model
def viewer(env, mode='initialize', filename='video', render=False):
    global render_buffer
    if mode == 'initialize':
        render_buffer = []
        mode = 'render'

    if mode == 'render':
        if render == True:
            env.render()
        curr_frame = env.render(mode='rgb_array')
        curr_frame = cv2.resize(curr_frame , (curr_frame.shape[1]//3 -1, curr_frame.shape[0]//3)) #"852x640"
        render_buffer.append(curr_frame)

    if mode == 'save':
        skvideo.io.vwrite(filename, np.asarray(render_buffer), outputdict={"-pix_fmt": "yuv420p"},verbosity=1)
        print("\n Video saved", filename)

#Reproduced saved actions from file in the env. Optionally save a video.
#Load file should include file path + filename + extension
#save_filename is the name for the video to be saved. It is stored under './data/videos/'
def reproduce_file_actions(load_file, save_folder = "./analysis/videos/reproduced_demonstrations/", save_filename = "video.mp4",show_video=True, save_video=False):
    data = load_actions_data(load_file)
    #Env init
    gym_env = gym.make('kitchen_relax-v1')
    env = gym_env.env
    s = env.reset()

    # prepare env
    env.sim.data.qpos[:] = data['init_qpos'].copy()
    env.sim.data.qvel[:] = data['init_qvel'].copy()
    env.sim.forward()

    #Viewer
    FPS = 30
    render_skip = max(1, round(1. / \
        (FPS * env.sim.model.opt.timestep * env.frame_skip)))
    viewer(env, mode='initialize')

    for i, action in enumerate(data['actions']):
        s , r, _, _ = env.step(action)
        if(i % render_skip == 0):
            viewer(env, mode='render', render=show_video)
    
    #save_video
    if(save_video):
        if not os.path.exists(save_folder):
                os.makedirs(save_folder)
        viewer(env, mode='save', filename=save_folder + save_filename)
    env.close()

def parse_reprod_act_vid():
    eval_filename = "./data/validation/friday_microwave_topknob_bottomknob_slide_0_path.pkl"
    demo_files = ["friday_topknob_bottomknob_switch_slide_0_path",
                "friday_microwave_topknob_bottomknob_hinge_0_path",
                "friday_microwave_kettle_topknob_switch_0_path",
                "friday_microwave_kettle_topknob_hinge_0_path",
                "friday_microwave_kettle_switch_slide_0_path",
                "friday_microwave_kettle_hinge_slide_0_path",
                "friday_microwave_kettle_bottomknob_slide_0_path",
                "friday_microwave_kettle_bottomknob_hinge_0_path",
                "friday_microwave_bottomknob_switch_slide_0_path",
                "friday_microwave_bottomknob_hinge_slide_0_path",
                "friday_kettle_topknob_switch_slide_0_path",
                "friday_kettle_topknob_bottomknob_slide_1_path",
                "friday_kettle_switch_hinge_slide_0_path",
                "friday_kettle_bottomknob_switch_slide_0_path",
                "friday_kettle_bottomknob_hinge_slide_0_path"
                ]

    for name in demo_files:
        #save training videos
        file_path = "./data/training/"+ name +".pkl"
        video_name = name[:-5] + "_demo.mp4"
        reproduce_file_actions(file_path, show_video=False, save_video=True, save_filename = video_name)

    #save evaluation file video
    reproduce_file_actions(eval_filename, show_video=False, save_video=True, save_filename = "friday_microwave_topknob_bottomknob_slide_eval_demo.mp4")

def test_model(model, goal_path, show_goal=False, env_steps = 1000, new_plan_frec = 20 , show_video = False,save_video=False, save_folder="./analysis/videos/model_trials/", save_filename="video.mp4"):
    #load goal
    goal = plt.imread(goal_path)#read as RGB, blue shelfs
    if(show_goal):
        plt.axis('off')
        plt.suptitle("Goal")
        plt.imshow(goal)
        plt.show()
    goal = np.rint(goal*255).astype(int) #change to model scale
    #Env init
    gym_env = gym.make('kitchen_relax-v1')
    env = gym_env.env
    
    s = env.reset()
    #init viewer utility
    FPS = 10
    render_skip = max(1, round(1. / \
        (FPS * env.sim.model.opt.timestep * env.frame_skip)))
    viewer(env, mode='initialize')

    #take actions
    for i in tqdm(range(env_steps)):
        curr_img = env.render(mode='rgb_array')   
        curr_img = cv2.resize(curr_img , (300,300))
        
        current_and_goal = np.stack((curr_img, goal) , axis=0) #(2, 300, 300, 3)
        current_and_goal = np.expand_dims(current_and_goal.transpose(0,3,1,2), axis=0) #(1, 2, 3, 300, 300)
        current_obs = np.expand_dims(s[:9], axis=0) #(1,9)

        #prediction
        if(i % new_plan_frec == 0):
            plan = model.get_pp_plan(current_obs,current_and_goal)
        action = model.predict_with_plan(current_obs, current_and_goal, plan).squeeze() #(9)
        s , r, _, _ = env.step(action.cpu().detach().numpy())
        if(i % render_skip == 0):
            viewer(env, mode='render', render=show_video)

    #Save model
    if(save_video):
        if not os.path.exists(save_folder):
            os.makedirs(save_folder)
        viewer(env, mode='save', filename=save_folder + save_filename)
    env.close()

def test_model_seq_goals(model, goal_lst, env_steps = 1000, new_plan_frec = 20 , \
    show_video = False,save_video=False, save_folder="./analysis/videos/model_trials/", save_filename="video.mp4"):
    print("Test subsequent goals... press n to change goal")
    print("Goal list:", goal_lst)
    new_goal = goal_lst.pop(0)
    print("first goal: ", new_goal)
    #load goal
    goal = plt.imread(new_goal) #read as RGB, blue shelfs
    goal = np.rint(goal*255).astype(int) #change to model scale

    #Env init
    gym_env = gym.make('kitchen_relax-v1')
    env = gym_env.env
    
    s = env.reset()
    #init viewer utility
    FPS = 10
    render_skip = max(1, round(1. / \
        (FPS * env.sim.model.opt.timestep * env.frame_skip)))
    viewer(env, mode='initialize')

    #take actions
    for i in tqdm(range(env_steps)):
        curr_img = env.render(mode='rgb_array')   
        curr_img = cv2.resize(curr_img , (300,300))
        
        current_and_goal = np.stack((curr_img, goal) , axis=0) #(2, 300, 300, 3)
        current_and_goal = np.expand_dims(current_and_goal.transpose(0,3,1,2), axis=0) #(1, 2, 3, 300, 300)
        current_obs = np.expand_dims(s[:9], axis=0) #(1,9)

        #prediction
        if(i % new_plan_frec == 0):
            plan = model.get_pp_plan(current_obs,current_and_goal)
        action = model.predict_with_plan(current_obs, current_and_goal, plan).squeeze() #(9)
        s , r, _, _ = env.step(action.cpu().detach().numpy())
        if(i % render_skip == 0):
            viewer(env, mode='render', render=show_video)
        
        ############ change goal #############
        if keyboard.is_pressed("n"):
            if(goal_lst): #list not empty
                new_goal = goal_lst.pop(0)
                print("Changing goal..")
                print("New goal", new_goal)
                print("Goals left:", goal_lst)
                goal = plt.imread(new_goal) #read as RGB, blue shelfs
                goal = np.rint(goal*255).astype(int) #change to model scale
                time.sleep(1)
                print("Continue execution..")
            else:
                print("empty goal list")
        if keyboard.is_pressed("e"):
            print("stopping execution..")
            break
        ########################################

        if(i % render_skip == 0):
            viewer(env, mode='render', render=show_video)
    #Save model
    if(save_video):
        if not os.path.exists(save_folder):
            os.makedirs(save_folder)
        viewer(env, mode='save', filename=save_folder + save_filename)
    env.close()

def test_subsequent():
    use_logistics = True
    #model init
    #10_logistic_multitask_bestaccb40k"
    model_name = "10_logistic_multitask_bestacc"
    model_file_path = './models/%s.pth'%model_name
    model = PlayLMP(num_mixtures=10, use_logistics=use_logistics)
    model.load(model_file_path)

    #test
    sample_new_plan = 1
    goal_lst = ["microwave", "/subsequent/microwave_bottomknob","/subsequent/microwave_bottomknob_topknob", "/subsequent/microwave_topknob_bottomknob_slide"]
    goal_lst = ["./data/goals/"+goal+".png" for goal in goal_lst]

    video_name = "Test_multiple_goals_1.mp4"
    test_model_seq_goals(model, goal_lst, env_steps = 700, new_plan_frec = sample_new_plan , \
                         show_video = True, save_video = True, save_filename=video_name)

def test():
    use_logistics = True
    
    #model init
    #model_file_path = './models/fit_10_logistic_multitask_accuracy.pth'
    model_name = "10_logistic_multitask_bestacc_new"
    model_file_path = './models/%s.pth'%model_name
    model = PlayLMP(num_mixtures=10, use_logistics=use_logistics)
    model.load(model_file_path)

    #[ "subsequent/kettle_bottomknob_slide", "subsequent/microwave_bottomknob_topknob", "subsequent/kettle_switch"]
    #goals = ["grip_hinge", "hinge" ]
    goals = ["microwave", "kettle", "bottomknob","slide","grip_hinge","hinge_2"]
    names = ["microwave", "kettle", "bottomknob","slide","grip_hinge","hinge_2"]
    save_parent_dir = "./analysis/videos/model_trials/"
    
    #create folders
    for name in names:
        save_folder = save_parent_dir + name +"/"
        if not os.path.exists(save_folder):
                    os.makedirs(save_folder)
    
    #Save videos
    n_runs = 5
    sample_new_plan = 1
    for goal,name in zip(goals,names):
        for i in range(n_runs): #save n_runs videos for each goal
            goal_file_path = "./data/goals/"+goal+".png"
            video_name = "%s_npf_%d_"%(model_name, sample_new_plan)+name+"(%d).mp4"%i
            test_model(model, goal_file_path, show_goal=False, env_steps=400, new_plan_frec=sample_new_plan, \
                        save_video=True, show_video = False, save_folder=save_parent_dir+name+"/", save_filename=video_name)

if __name__ == '__main__':
    #----------- Parser ------------#
    parser = argparse.ArgumentParser(description='some description')
    parser.add_argument('--goal_file_path', dest='goal_file_path', type=str, default='./data/goals/microwave.png')
    parser.add_argument('--model_name', dest='model_file_path', type=str, default='1_gaussian_multitask.pth')
    parser.add_argument('--use_logistics', dest='use_logistics', type=bool, default=True)
    args = parser.parse_args()
    #print(args)
    #-------------------------------#
    
    # test_subsequent()
    # Good models
    # mws_1_gaussian_multitask_b77100
    # mws_1_gaussian_multitask_b41350
    test()

    #----------- Save videos from reproduce files .pkl ------------#
    # name = "friday_kettle_bottomknob_hinge_slide_1_path"
    # file_path = "./data/training/"+ name +".pkl"
    # video_name = "./analysis/videos/reproduced_demonstrations"
    # reproduce_file_actions(file_path, show_video=True, save_video=False, save_filename = video_name)

    #----------- Print images from val packages ------------#
    # print_img_goals(data_dir = "./data/validation/", i=0, n_packages=1)