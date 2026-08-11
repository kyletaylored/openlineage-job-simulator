"""Entry point for a leaf task step: no children, so start + work + terminal
all happen in one script -- the same shape as app.job_simulator._run_node's
leaf-node case (children_spec=None), just as a standalone Azure ML step
instead of a recursive call.
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
    p.add_argument("--duration-min", type=float, required=True)
    p.add_argument("--duration-max", type=float, required=True)
    p.add_argument("--failure-rate", type=float, required=True)
    p.add_argument("--force-fail", action="store_true")
    p.add_argument("--parent-run-id", required=True)
    p.add_argument("--parent-name", required=True)
    p.add_argument("--root-run-id", required=True)
    p.add_argument("--root-name", required=True)
    p.add_argument("--status-out", required=True)
    return p.parse_args()


def main():
    args = parse_args()
    common.configure()

    with common.traced_step("task", run_id=args.run_id, name=args.name):
        run_facets = common.build_run_facets(
            parent_run_id=args.parent_run_id, parent_name=args.parent_name,
            root_run_id=args.root_run_id, root_name=args.root_name,
            namespace=args.namespace,
        )
        common.emit_start(
            namespace=args.namespace, name=args.name, run_id=args.run_id,
            job_type="TASK", ol_service=common.ol_service_name("TASK"),
            run_facets=run_facets,
        )
        time.sleep(random.uniform(args.duration_min, args.duration_max))

        will_fail = common.decide_failure(
            force_fail=args.force_fail, failure_rate=args.failure_rate,
            any_child_failed=False, fail_on_child_fail=False,
        )
        status, error_message = common.emit_terminal(
            namespace=args.namespace, name=args.name, run_id=args.run_id,
            job_type="TASK", ol_service=common.ol_service_name("TASK"),
            run_facets=run_facets, will_fail=will_fail, any_child_failed=False,
            fail_on_child_fail=False, role_label="task",
        )
        common.write_status(args.status_out, status, error_message)


if __name__ == "__main__":
    main()
