import json
import random

import numpy as np
import matplotlib.pyplot as plt

import torch
import torch.nn.functional as F
import torch.optim as optim

from model.buffer import ReplayBuffer
from model.network import QNetwork


BATCH_SIZE = 256
BUFFER_CAPACITY = 100000
DISCOUNT_FACTOR = 0.99
EPSILON_DECAY = 0.999999
EPSILON_END = 0.01
EPSILON_START = 1.0
LEARNING_RATE = 0.001
TARGET_UPDATE = 2500
TOTAL_EPISODES = 250000


class LaurelAgent(object):

    def __init__(self, env):

        self.env = env
        self.epsilon = EPSILON_END
    
        self.dim_state = self.env.observation_space.shape
        self.dim_state = (self.env.observation_space.high.max(), self.dim_state[0], self.dim_state[1])

        self.dim_action = self.env.action_space.n

    def initialize_network(self, model_path):

        self.policy_net = QNetwork(self.dim_state, self.dim_action).cuda()
        self.target_net = QNetwork(self.dim_state, self.dim_action).cuda()

        if model_path is not None:

            state_dict = torch.load(model_path)
            self.policy_net.load_state_dict(state_dict)

        self.target_net.load_state_dict(self.policy_net.state_dict())
        self.target_net.eval()
    
    def act(self, state):

        if self.epsilon > EPSILON_END:
            self.epsilon = self.epsilon * EPSILON_DECAY

        if random.random() > self.epsilon:
            with torch.no_grad():
                return self.policy_net(state).max(1)[1].view(1)
        
        return torch.tensor([random.randrange(self.dim_action)]).long().cuda()

    def initialize_training(self):

        self.optimizer = optim.Adam(self.policy_net.parameters(), lr=LEARNING_RATE)
        self.buffer = ReplayBuffer(BUFFER_CAPACITY)
        self.epsilon = EPSILON_START

        self.policy_net.train()

    def initialize_demo(self, demo_url):

        with open(demo_url) as demo_data:

            demo = json.load(demo_data)
            human_actions = demo['human_actions']

            state = self.env.reset()
            state = self.convert_state(state)

            cumulative_reward = 0

            for action in human_actions:

                action = torch.tensor([action]).long().cuda()
                next_state, reward, done, _ = self.env.step((action.item(), 0))

                cumulative_reward = cumulative_reward + reward

                next_state = self.convert_state(next_state)
                reward = torch.tensor([reward]).float().cuda()

                for _ in range(1000):
                    self.buffer.push(state, action, reward, next_state, done)
                
                state = next_state

                if done: break

        loss = 0

        for i_episode in range(10000):

            loss = self.optimize()

            if (i_episode + 1) % TARGET_UPDATE == 0:
                
                self.target_net.load_state_dict(self.policy_net.state_dict())

        print("Pretraining with demostration... Completed with loss %.6f" % loss)

        self.target_net.load_state_dict(self.policy_net.state_dict())
    
    def train(self, model_path):

        losses = []
        rewards = []

        for i_episode in range(TOTAL_EPISODES):

            state = self.env.reset()
            state = self.convert_state(state)

            cumulative_reward = 0
            cumulative_loss = 0
            
            while True:
                
                action = self.act(state)
                next_state, reward, done, _ = self.env.step((action.item(), 0))

                cumulative_reward = cumulative_reward + reward

                next_state = self.convert_state(next_state)
                reward = torch.tensor([reward]).float().cuda()

                self.buffer.push(state, action, reward, next_state, done)

                loss = self.optimize()
                cumulative_loss = cumulative_loss + loss
                
                state = next_state

                if done: break
            
            rewards.append(cumulative_reward)
            losses.append(cumulative_loss)

            if cumulative_reward > 0:

                with open("winner.txt", "a+") as winner_file:
                   
                    winner_file.write("%06d   %d\n" % (i_episode, cumulative_reward))

            if i_episode % 100 == 0:

                average_loss = np.average(losses)
                average_reward = np.average(rewards)

                print("Training in progress... %06d/%06d  Loss: %.6f  Reward: %.6f  Epsilon: %.6f" %
                        (i_episode, TOTAL_EPISODES, average_loss, average_reward, self.epsilon))

            if i_episode % TARGET_UPDATE == 0:

                torch.save(self.policy_net.state_dict(), model_path)
                self.target_net.load_state_dict(self.policy_net.state_dict())

        
    def optimize(self):

        if len(self.buffer) < BATCH_SIZE: return
    
        transitions = self.buffer.sample(BATCH_SIZE)
        batch = list(zip(*transitions))

        state_batch = torch.stack(batch[0])
        action_batch = torch.stack(batch[1])
        reward_batch = torch.stack(batch[2])
        next_state_batch = torch.stack(batch[3])

        non_final_mask = [not done for done in batch[4]]
        non_final_next_states = next_state_batch[non_final_mask]

        state_values = self.policy_net(state_batch).gather(1, action_batch)
        next_state_values = torch.zeros((BATCH_SIZE, 1)).cuda()

        next_actions = self.policy_net(non_final_next_states).max(1)[1].view(-1, 1)
        next_state_values[non_final_mask] = self.target_net(non_final_next_states).gather(1, next_actions).view(-1, 1)
        
        expected_state_values = reward_batch + (DISCOUNT_FACTOR * next_state_values)
        
        loss = F.smooth_l1_loss(state_values, expected_state_values)
        
        self.optimizer.zero_grad()
        loss.backward()

        for param in self.policy_net.parameters():
            param.grad.data.clamp_(-1, 1)
        
        self.optimizer.step()

        return loss.item()

    def initialize_testing(self):
        
        self.optimizer = None
        self.buffer = None
        self.epsilon = EPSILON_END

        self.policy_net.eval()
    
    def test(self):

        rewards = []

        for i_episode in range(TOTAL_EPISODES):

            state = self.env.reset()
            state = self.convert_state(state)

            cumulative_reward = 0
            
            while True:
                
                action = self.act(state)
                state, reward, done, _ = self.env.step((action.item(), 0))
                state = self.convert_state(state)

                cumulative_reward = cumulative_reward + reward

                if done: break
            
            rewards.append(cumulative_reward)

            if i_episode % TARGET_UPDATE == 0: print(i_episode, np.mean(rewards))
        
    def convert_state(self, state):

        state = torch.from_numpy(state).long().cuda()
        state = F.one_hot(state, int(self.dim_state[0]))
        state = state.permute(2, 0, 1).float()

        return state


class DDQNAgent(object):

    def __init__(self, env):

        self.env = env
        self.epsilon = EPSILON_END
    
        self.dim_state = self.env.observation_space.shape
        self.dim_state = (self.env.observation_space.high.max(), self.dim_state[0], self.dim_state[1])

        self.dim_action = self.env.action_space.n

    def initialize_network(self, model_path):

        self.policy_net = QNetwork(self.dim_state, self.dim_action).cuda()
        self.target_net = QNetwork(self.dim_state, self.dim_action).cuda()

        if model_path is not None:

            state_dict = torch.load(model_path)
            self.policy_net.load_state_dict(state_dict)

        self.target_net.load_state_dict(self.policy_net.state_dict())
        self.target_net.eval()
    
    def act(self, state):

        if self.epsilon > EPSILON_END:
            self.epsilon = self.epsilon * EPSILON_DECAY

        if random.random() > self.epsilon:
            with torch.no_grad():
                return self.policy_net(state).max(1)[1].view(1)
        
        return torch.tensor([random.randrange(self.dim_action)]).long().cuda()

    def initialize_training(self):

        self.optimizer = optim.Adam(self.policy_net.parameters(), lr=LEARNING_RATE)
        self.buffer = ReplayBuffer(BUFFER_CAPACITY)
        self.epsilon = EPSILON_START

        self.policy_net.train()

    def initialize_demo(self, demo_url):

        with open(demo_url) as demo_data:

            demo = json.load(demo_data)
            human_actions = demo['human_actions']

            state = self.env.reset()
            state = self.convert_state(state)

            cumulative_reward = 0

            for action in human_actions:

                action = torch.tensor([action]).long().cuda()
                next_state, reward, done, _ = self.env.step((action.item(), 0))

                cumulative_reward = cumulative_reward + reward

                next_state = self.convert_state(next_state)
                reward = torch.tensor([reward]).float().cuda()

                for _ in range(1000):
                    self.buffer.push(state, action, reward, next_state, done)
                
                state = next_state

                if done: break

        loss = 0

        for i_episode in range(10000):

            loss = self.optimize()

            if (i_episode + 1) % TARGET_UPDATE == 0:
                
                self.target_net.load_state_dict(self.policy_net.state_dict())

        print("Pretraining with demostration... Completed with loss %.6f" % loss)

        self.target_net.load_state_dict(self.policy_net.state_dict())
    
    def train(self, model_path):

        losses = []
        rewards = []

        for i_episode in range(TOTAL_EPISODES):

            state = self.env.reset()
            state = self.convert_state(state)

            cumulative_reward = 0
            cumulative_loss = 0
            
            while True:
                
                action = self.act(state)
                next_state, reward, done, _ = self.env.step((action.item(), 0))

                cumulative_reward = cumulative_reward + reward

                next_state = self.convert_state(next_state)
                reward = torch.tensor([reward]).float().cuda()

                self.buffer.push(state, action, reward, next_state, done)

                loss = self.optimize()
                cumulative_loss = cumulative_loss + loss
                
                state = next_state

                if done: break
            
            rewards.append(cumulative_reward)
            losses.append(cumulative_loss)

            if cumulative_reward > 0:

                with open("winner.txt", "a+") as winner_file:
                   
                    winner_file.write("%06d   %d\n" % (i_episode, cumulative_reward))

            if i_episode % 100 == 0:

                average_loss = np.average(losses)
                average_reward = np.average(rewards)

                print("Training in progress... %06d/%06d  Loss: %.6f  Reward: %.6f  Epsilon: %.6f" %
                        (i_episode, TOTAL_EPISODES, average_loss, average_reward, self.epsilon))

            if i_episode % TARGET_UPDATE == 0:

                torch.save(self.policy_net.state_dict(), model_path)
                self.target_net.load_state_dict(self.policy_net.state_dict())

        
    def optimize(self):

        if len(self.buffer) < BATCH_SIZE: return
    
        transitions = self.buffer.sample(BATCH_SIZE)
        batch = list(zip(*transitions))

        state_batch = torch.stack(batch[0])
        action_batch = torch.stack(batch[1])
        reward_batch = torch.stack(batch[2])
        next_state_batch = torch.stack(batch[3])

        non_final_mask = [not done for done in batch[4]]
        non_final_next_states = next_state_batch[non_final_mask]

        state_values = self.policy_net(state_batch).gather(1, action_batch)
        next_state_values = torch.zeros((BATCH_SIZE, 1)).cuda()

        next_actions = self.policy_net(non_final_next_states).max(1)[1].view(-1, 1)
        next_state_values[non_final_mask] = self.target_net(non_final_next_states).gather(1, next_actions).view(-1, 1)
        
        expected_state_values = reward_batch + (DISCOUNT_FACTOR * next_state_values)
        
        loss = F.smooth_l1_loss(state_values, expected_state_values)
        
        self.optimizer.zero_grad()
        loss.backward()

        for param in self.policy_net.parameters():
            param.grad.data.clamp_(-1, 1)
        
        self.optimizer.step()

        return loss.item()

    def initialize_testing(self):
        
        self.optimizer = None
        self.buffer = None
        self.epsilon = EPSILON_END

        self.policy_net.eval()
    
    def test(self):

        rewards = []

        for i_episode in range(TOTAL_EPISODES):

            state = self.env.reset()
            state = self.convert_state(state)

            cumulative_reward = 0
            
            while True:
                
                action = self.act(state)
                state, reward, done, _ = self.env.step((action.item(), 0))
                state = self.convert_state(state)

                cumulative_reward = cumulative_reward + reward

                if done: break
            
            rewards.append(cumulative_reward)

            if i_episode % TARGET_UPDATE == 0: print(i_episode, np.mean(rewards))
        
    def convert_state(self, state):

        state = torch.from_numpy(state).long().cuda()
        state = F.one_hot(state, int(self.dim_state[0]))
        state = state.permute(2, 0, 1).float()

        return state
