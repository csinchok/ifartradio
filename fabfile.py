import os

from fabric.api import *
from fabric.operations import put
from fabric.contrib.project import rsync_project

LOCAL_DIR = '/tmp/ifartradio'
REMOTE_DIR = '/www/ifartradio'

env.user = 'ifartradio'
env.hosts = ['66.175.213.211']

def update_requirements():
	with cd(REMOTE_DIR), prefix('source bin/activate'):
		run('pip install -r %s' % os.path.join(REMOTE_DIR, 'app', 'requirements.txt'))


def archive():
	local('mkdir -p %s' % LOCAL_DIR)
	local('git archive HEAD | tar -x -C %s' % LOCAL_DIR)

def push():
	put(os.path.join(LOCAL_DIR, "supervisord.conf"), os.path.join(REMOTE_DIR, "supervisord.conf"))
	for subdir in ['static/', 'app/']:
		rsync_project(os.path.join(REMOTE_DIR, subdir), local_dir=os.path.join(LOCAL_DIR, subdir), delete=True, exclude="settings.py")

def cleanup():
	local('rm -r %s' % LOCAL_DIR)

def deploy():
	archive()
	push()