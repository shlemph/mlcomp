from collections import OrderedDict
import os
import yaml

from mlcomp.db.providers import *
from mlcomp.db.enums import TaskType
from mlcomp.db.models import *
from mlcomp.db import TaskProvider
from mlcomp.worker.executors import Executor
from mlcomp.worker.storage import Storage


def dag_standard(config: dict,
                 debug: bool,
                 config_text: str = None,
                 upload_files: bool = True,
                 copy_files_from: int = None
                 ):
    info = config['info']
    provider = TaskProvider()
    report_provider = ReportProvider()
    report_tasks_provider = ReportTasksProvider()
    report_scheme_provider = ReportSchemeProvider()
    schemes = report_scheme_provider.all()

    storage = Storage()
    dag_provider = DagProvider()

    type = info.get('type', 'Standard')
    assert type in DagType.names(), 'unknown dag type, ' + type

    project = ProjectProvider().by_name(info['project']).id
    dag = dag_provider.add(Dag(config=config_text or yaml.dump(config),
                               project=project,
                               name=info['name'],
                               docker_img=info.get('docker_img'),
                               type=DagType.from_name(type)
                               ))
    if upload_files:
        folder = os.getcwd()
        storage.upload(folder, dag)
    elif copy_files_from:
        storage.copy_from(copy_files_from, dag)

    created = OrderedDict()
    executors = config['executors']

    while len(created) < len(executors):
        for k, v in executors.items():
            valid = True
            if 'depends' in k:
                for d in v['depends']:
                    if d not in executors:
                        raise Exception(
                            f'Executor {k} depend on {d} which does not exist')

                    valid = valid and d in created
            if valid:
                task_type = TaskType.User.value
                if v.get('task_type') == 'train' or \
                        Executor.is_trainable(v['type']):
                    task_type = TaskType.Train.value

                task = Task(
                    name=f'{k}',
                    executor=k,
                    computer=info.get('computer'),
                    gpu=v.get('gpu', 0),
                    cpu=v.get('cpu', 1),
                    memory=v.get('memory', 0.1),
                    dag=dag.id,
                    debug=debug,
                    steps=int(v.get('steps', '1')),
                    type=task_type
                )

                if v.get('report'):
                    if v['report'] not in schemes:
                        raise Exception(f'Unknown report = {v["report"]}')

                    report_config = schemes[v['report']]
                    additional_info = {'report_config': report_config}
                    task.additional_info = pickle.dumps(additional_info)
                    provider.add(task)
                    report = Report(config=json.dumps(report_config),
                                    name=task.name,
                                    project=project)
                    report_provider.add(report)
                    report_tasks_provider.add(
                        ReportTasks(report=report.id, task=task.id))
                else:
                    provider.add(task)

                created[k] = task.id

                if 'depends' in v:
                    for d in v['depends']:
                        provider.add_dependency(created[k], created[d])
    return created


def dag_pipe(config: dict, config_text: str = None):
    assert 'interfaces' in config, 'interfaces missed'
    assert 'pipes' in config, 'pipe missed'

    info = config['info']

    storage = Storage()
    dag_provider = DagProvider()

    type = info.get('type', 'Standard')
    assert type in DagType.names(), 'unknown dag type, ' + type

    folder = os.getcwd()
    project = ProjectProvider().by_name(info['project']).id
    dag = dag_provider.add(Dag(config=config_text,
                               project=project,
                               name=info['name'],
                               docker_img=info.get('docker_img'),
                               type=DagType.from_name(type)
                               ))
    storage.upload(folder, dag)


def dag_model_add(data: dict):
    task_provider = TaskProvider()
    task = task_provider.by_id(data['task'], options=joinedload(Task.dag_rel))
    project = ProjectProvider().by_id(task.dag_rel.project)
    config = {
        'info': {
            'name': 'model_add',
            'project': project.name
        },
        'executors': {
            'model_add': {
                'type': 'model_add',
                'dag': data['dag'],
                'slot': data['slot'],
                'interface': data['interface'],
                'task': data.get('task'),
                'name': data['name']
            }
        }
    }

    dag_standard(config, debug=False, upload_files=False)


def dag_model_start(data: dict):
    dag = DagProvider().by_id(data['dag'], joined_load=[Dag.project_rel])
    project = dag.project_rel
    src_config = Config.from_yaml(dag.config)
    config = {
        'info': {
            'name': data['pipe'],
            'project': project.name
        },
        'executors': {
            src_config['pipes'][data['pipe']]
        }
    }

    dag_standard(config,
                 debug=False,
                 upload_files=False,
                 copy_files_from=data['dag']
                 )