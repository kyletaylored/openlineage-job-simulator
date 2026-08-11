"""Entry point for controller_start / worker_start pipeline steps.

Emits the OpenLineage START event for this node, then sleeps to simulate its
own overhead -- exactly the "own overhead" phase of
app.job_simulator._run_node, before it dispatches children. Dispatch of
children is not this script's job: it's just the pipeline DAG's next steps
(see azureml/submit_pipeline.py). The matching *_finalize step
(run_node_finalize.py) runs after those children complete and emits this
node's terminal event.
"""
import argparse

import random
import time

from azureml.steps import common


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--namespace", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--job-type", required=True, choices=["JOB", "TASK"])
    p.add_argument("--duration-min", type=float, required=True)
    p.add_argument("--duration-max", type=float, required=True)
    p.add_argument("--root-run-id", required=True)
    p.add_argument("--root-name", required=True)
    p.add_argument("--parent-run-id", default=None)
    p.add_argument("--parent-name", default=None)
    return p.parse_args()


def main():
    args = parse_args()
    common.configure()

    resource = "controller_start" if args.job_type == "JOB" else "worker_start"
    with common.traced_step(resource, run_id=args.run_id, name=args.name):
        run_facets = common.build_run_facets(
            parent_run_id=args.parent_run_id, parent_name=args.parent_name,
            root_run_id=args.root_run_id, root_name=args.root_name,
            namespace=args.namespace,
        )
        common.emit_start(
            namespace=args.namespace, name=args.name, run_id=args.run_id,
            job_type=args.job_type, ol_service=common.ol_service_name(args.job_type),
            run_facets=run_facets,
        )
        time.sleep(random.uniform(args.duration_min, args.duration_max))


if __name__ == "__main__":
    main()
