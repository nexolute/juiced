import gym
import torch

from model.agent import DDQNAgent

from juiced.level import Level
from juiced.juiced import JuicedEnv

# enable cuda

device = torch.device('cuda')

# create environment

env = JuicedEnv("small")
env.reset()

# create agent

agent = DDQNAgent(env)
agent.initialize_network(None)
agent.initialize_training()
agent.train('./model/policy.pth')
