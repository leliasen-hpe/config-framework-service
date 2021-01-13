# Copyright 2020-2021 Hewlett Packard Enterprise Development LP

import connexion
from datetime import datetime
import logging
import os
import subprocess
import tempfile

from cray.cfs.api import dbutils
from cray.cfs.api.controllers import components

LOGGER = logging.getLogger('cray.cfs.api.controllers.configurations')
DB = dbutils.get_wrapper(db='configurations')
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dbutils.redis_error_handler
def get_configurations(in_use=None):
    """Used by the GET /configurations API operation"""
    LOGGER.debug("GET /configurations invoked get_configurations")
    response = _get_configurations_data(in_use=in_use)
    return response, 200


def _get_configurations_data(in_use=None):
    response = DB.get_all()
    if in_use is not None:
        in_use_list = _get_in_use_list()
        response = [config for config in response if (config['name'] in in_use_list) == in_use]
    return response


def _get_in_use_list():
    in_use_list = set()
    components_data = components.get_components_data()
    for component in components_data:
        desiredState = component.get('desiredState', '')
        if desiredState and type(desiredState) == str:
            in_use_list.add(desiredState)
    return list(in_use_list)


@dbutils.redis_error_handler
def get_configuration(configuration_id):
    """Used by the GET /configurations/{configuration_id} API operation"""
    LOGGER.debug("GET /configurations/id invoked get_configuration")
    if configuration_id not in DB:
        return connexion.problem(
            status=404, title="Configuration could not found.",
            detail="Configuration {} could not be found".format(configuration_id))
    return DB.get(configuration_id), 200


@dbutils.redis_error_handler
def put_configuration(configuration_id):
    """Used by the PUT /configurations/{configuration_id} API operation"""
    LOGGER.debug("PUT /configurations/id invoked put_configuration")
    try:
        data = connexion.request.get_json()
    except Exception as err:
        return connexion.problem(
            status=400, title="Error parsing the data provided.",
            detail=str(err))

    for layer in data.get('layers'):
        if 'branch' in layer and 'commit' in layer:
            return connexion.problem(
                status=400, title="Error handling error branches",
                detail='Only branch or commit should be specified for each layer, not both.')
    try:
        data = _set_auto_fields(data)
    except subprocess.CalledProcessError as e:
        return connexion.problem(
            status=400, title="Error converting branch name to commit.",
            detail=str(e))

    data['name'] = configuration_id
    return DB.put(configuration_id, data), 200


@dbutils.redis_error_handler
def patch_configuration(configuration_id):
    """Used by the PATCH /configurations/{configuration_id} API operation"""
    LOGGER.debug("PATCH /configurations/id invoked put_configuration")
    if configuration_id not in DB:
        return connexion.problem(
            status=404, title="Configuration could not found.",
            detail="Configuration {} could not be found".format(configuration_id))
    data = DB.get(configuration_id)

    try:
        data = _set_auto_fields(data)
    except subprocess.CalledProcessError as e:
        return connexion.problem(
            status=400, title="Error converting branch name to commit.",
            detail=str(e))

    return DB.put(configuration_id, data), 200


@dbutils.redis_error_handler
def delete_configuration(configuration_id):
    """Used by the DELETE /configurations/{configuration_id} API operation"""
    LOGGER.debug("DELETE /configurations/id invoked delete_configuration")
    if configuration_id not in DB:
        return connexion.problem(
            status=404, title="Configuration could not found.",
            detail="Configuration {} could not be found".format(configuration_id))
    if configuration_id in _get_in_use_list():
        return connexion.problem(
            status=400, title="Configuration is in use.",
            detail="Configuration {} is referenced by the desired state of"
                   "some components".format(configuration_id))
    return DB.delete(configuration_id), 204


def _set_auto_fields(data):
    data = _set_last_updated(data)
    data = _convert_branches_to_commits(data)
    return data


def _set_last_updated(data):
    data['lastUpdated'] = datetime.now().strftime(TIME_FORMAT)
    return data


def _convert_branches_to_commits(data):
    for layer in data.get('layers'):
        if 'branch' in layer:
            layer['commit'] = _get_commit_id(layer.get('cloneUrl'), layer.get('branch'))
    return data


def _get_commit_id(repo_url, branch):
    """
    Given a branch and git url, returns the commit id at the top of that branch

    Args:
      repo_url: The cloneUrl to pass to the CFS session
      branch: The branch to pass to the CFS session

    Returns:
      commit: A commit id for the given branch

    Raises:
      subprocess.CalledProcessError -- for errors encountered calling git
    """
    with tempfile.TemporaryDirectory(dir='/tmp') as tmp_dir:
        repo_name = repo_url.split('/')[-1].split('.')[0]
        repo_dir = os.path.join(tmp_dir, repo_name)

        clone_command = 'git clone {}'.format(repo_url).split()
        checkout_command = 'git checkout {}'.format(branch).split()
        parse_command = 'git rev-parse HEAD'.split()
        try:
            subprocess.check_call(clone_command, cwd=tmp_dir,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(checkout_command, cwd=repo_dir,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            output = subprocess.check_output(parse_command, cwd=repo_dir)
            commit = output.decode("utf-8").strip()
            LOGGER.info('Translated git branch {} to commit {}'.format(branch, commit))
            return commit
        except subprocess.CalledProcessError as e:
            LOGGER.error('Failed interacting with the specified cloneUrl: {}'.format(e))
            raise


class Configurations(object):
    def __init__(self):
        self.configs = {}

    """Helper class for other endpoints that need access to configurations"""
    def get_config(self, key):
        if key not in self.configs:
            self.configs[key] = DB.get(key)
        return self.configs[key]
