"""Reproduce a source-bound native visual pilot in a new, empty output directory.

Run with the repository's virtualenv. Requires Blender and executes trusted code.
Raw native files/logs are local evidence, not sanitized public downloads.
"""
import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / 'src'))
from studio_agent_bridge.engine import Engine  # noqa: E402

SOURCE_SHA256 = 'a2c2b4fcecb37596c264ac4fd4a39ceb32033206b862028b41684d2eb9a679d6'


def prepare(root, archive):
    if root.exists() and any(root.iterdir()):
        raise ValueError('Output directory must be empty; previous evidence is never replaced')
    with zipfile.ZipFile(archive) as bundle:
        info = bundle.getinfo('industrial-lab/scene-day.blend')
        if info.file_size > 16_000_000:
            raise ValueError('Unexpectedly large input scene')
        data = bundle.read(info)
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError('Input does not match the reviewed published lab')
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / 'workspace'
    (workspace / 'source').mkdir(parents=True)
    (workspace / 'scripts').mkdir()
    (workspace / 'source/scene-day.blend').write_bytes(data)
    shutil.copyfile(Path(__file__).with_name('build_station.py'), workspace / 'scripts/build_station.py')
    return workspace


def run(root, archive):
    workspace = prepare(root, archive)
    engine = Engine(workspace, {'allow_scripts': True, 'blender_timeout_seconds': 900})
    job = engine.create_job('inspection-pilot', 'blender', 'Matched before/after visual pilot and fresh readback')
    state = {'job_id': job['id'], 'source_sha256': SOURCE_SHA256, 'actions': {}}

    def save():
        (root / 'state.json').write_text(json.dumps(state, indent=2) + '\n')

    def act(label, source, digest, mode, render):
        revision = engine.inspect_job(job['id'])['revision']
        result = engine.execute_action(job['id'], 'run_script', {
            'script_path': 'scripts/build_station.py', 'input_blend': source,
            'expected_source_sha256': digest, 'script_parameters': {'mode': mode, 'render': render},
            'render': False,
        }, revision)
        state['actions'][label] = result
        save()
        print(label, result['status'], flush=True)
        if result['status'] != 'verified':
            raise RuntimeError('Inspect preserved state and native output; do not automatically replay')
        return result

    save()
    act('baseline', 'source/scene-day.blend', SOURCE_SHA256, 'baseline', True)
    built = act('build', 'source/scene-day.blend', SOURCE_SHA256, 'build', True)
    native = next(a for a in built['result']['artifacts']
                  if a['kind'] == 'blend' and '/source_snapshot/' not in a['path'])
    act('fresh_reopen', native['path'], native['sha256'], 'inspect', False)
    reports = {k: json.loads((workspace / v['run_dir'] / 'workcell-evidence.json').read_text())
               for k, v in state['actions'].items()}
    if len({r['original_fields_sha256'] for r in reports.values()}) != 1:
        raise RuntimeError('Original mesh observations changed')
    if reports['build']['station_meshes'] != reports['fresh_reopen']['station_meshes']:
        raise RuntimeError('Added assembly did not survive reopening')
    if hashlib.sha256((workspace / 'source/scene-day.blend').read_bytes()).hexdigest() != SOURCE_SHA256:
        raise RuntimeError('Source bytes changed')
    state['comparison'] = {'reports': reports, 'source_bytes_unchanged': True,
                           'original_fields_equal_across_3_processes': True,
                           'unreal_transfer': 'not_run', 'human_visual_acceptance': 'pending'}
    save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--archive', type=Path, default=REPOSITORY / 'web/public/assets/environment/industrial-lab-native-v2.zip')
    args = parser.parse_args()
    run(args.output.resolve(), args.archive.resolve())
