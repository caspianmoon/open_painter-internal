"""Measure Blender node-tree construction cost by tree size and structure.

Run headless (node trees need no GPU)::

    blender --background --factory-startup --python-exit-code 1 \
      --python scripts/benchmark_node_graph_scaling.py -- --report .blender-test/node-graph-scaling.json

Three layouts build the same number of ShaderNodeMath nodes with chained links:
``flat`` (one tree), ``grouped`` (K separate group trees instanced from a top
tree, like Ucupaint's per-layer groups) and ``shared`` (one library group tree
built once and instanced K times, like Ucupaint's ``lib.blend`` groups). Wall
time covers ``nodes.new`` + renaming + ``links.new`` only. It is a construction
benchmark: GPU shader compilation of the resulting material is not measured.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def chain(tree, count, prefix):
    previous = None
    for index in range(count):
        node = tree.nodes.new('ShaderNodeMath')
        node.name = f'{prefix}{index:06d}'
        node.operation = 'ADD'
        if previous is not None:
            tree.links.new(previous.outputs[0], node.inputs[0])
        previous = node
    return previous


def flat(total):
    tree = bpy.data.node_groups.new('flat', 'ShaderNodeTree')
    started = time.perf_counter()
    chain(tree, total, 'f')
    seconds = time.perf_counter() - started
    bpy.data.node_groups.remove(tree)
    return seconds


def grouped(total, groups):
    top = bpy.data.node_groups.new('top', 'ShaderNodeTree')
    trees = []
    started = time.perf_counter()
    previous = None
    for g in range(groups):
        tree = bpy.data.node_groups.new(f'group{g}', 'ShaderNodeTree')
        trees.append(tree)
        tree.interface.new_socket(name='In', in_out='INPUT', socket_type='NodeSocketFloat')
        tree.interface.new_socket(name='Out', in_out='OUTPUT', socket_type='NodeSocketFloat')
        entry = tree.nodes.new('NodeGroupInput')
        last = chain(tree, total // groups, f'g{g}_')
        exit_node = tree.nodes.new('NodeGroupOutput')
        tree.links.new(entry.outputs[0], tree.nodes[f'g{g}_000000'].inputs[1])
        tree.links.new(last.outputs[0], exit_node.inputs[0])
        instance = top.nodes.new('ShaderNodeGroup')
        instance.name = f'inst{g:04d}'
        instance.node_tree = tree
        if previous is not None:
            top.links.new(previous.outputs[0], instance.inputs[0])
        previous = instance
    seconds = time.perf_counter() - started
    for tree in trees:
        bpy.data.node_groups.remove(tree)
    bpy.data.node_groups.remove(top)
    return seconds


def shared(total, instances):
    top = bpy.data.node_groups.new('top', 'ShaderNodeTree')
    started = time.perf_counter()
    library = bpy.data.node_groups.new('library', 'ShaderNodeTree')
    library.interface.new_socket(name='In', in_out='INPUT', socket_type='NodeSocketFloat')
    library.interface.new_socket(name='Out', in_out='OUTPUT', socket_type='NodeSocketFloat')
    entry = library.nodes.new('NodeGroupInput')
    last = chain(library, total // instances, 'lib')
    exit_node = library.nodes.new('NodeGroupOutput')
    library.links.new(entry.outputs[0], library.nodes['lib000000'].inputs[1])
    library.links.new(last.outputs[0], exit_node.inputs[0])
    previous = None
    for i in range(instances):
        instance = top.nodes.new('ShaderNodeGroup')
        instance.name = f'inst{i:04d}'
        instance.node_tree = library
        if previous is not None:
            top.links.new(previous.outputs[0], instance.inputs[0])
        previous = instance
    seconds = time.perf_counter() - started
    bpy.data.node_groups.remove(library)
    bpy.data.node_groups.remove(top)
    return seconds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, default=ROOT / '.blender-test/node-graph-scaling.json')
    parser.add_argument('--sizes', type=int, nargs='+', default=[500, 1000, 2000, 3000])
    parser.add_argument('--groups', type=int, default=10)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    rows = []
    for total in args.sizes:
        row = dict(nodes=total, flat_s=round(flat(total), 3),
                   grouped_s=round(grouped(total, args.groups), 3),
                   shared_s=round(shared(total, args.groups), 3), groups=args.groups)
        rows.append(row)
        print(json.dumps(row), flush=True)
    report = dict(blender=bpy.app.version_string, build=bpy.app.build_hash.decode(),
                  note='Construction wall time only (nodes.new, rename, links.new); no GPU compile.',
                  rows=rows)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding="utf-8")
    print('NODE_GRAPH_SCALING', json.dumps(report), flush=True)


main()
