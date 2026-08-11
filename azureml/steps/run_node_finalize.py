"""Entry point for controller_finalize / worker_finalize pipeline steps.

Runs after all of this node's children have completed. Reads each child's
status output (written by that child's own *_finalize or task step),
aggregates pass/fail exactly like app.job_simulator._run_node's
any_child_failed/fail_parent_on_child_fail cascade check, rolls this node's
own random failure_rate (the same check _run_node makes only after children
finish, not before dispatch), and emits the OpenLineage terminal event.
Writes its own status output so an ancestor's finalize step can aggregate it
in turn.
"""
import argparse

from azureml.steps import common


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--namespace", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--job-type", required=True, choices=["JOB", "TASK"])
    p.add_argument("--failure-rate", type=float, required=True)
    p.add_argument("--force-fail", action="store_true")
    p.add_argument("--fail-on-child-fail", action="store_true")
    p.add_argument("--role-label", required=True,
                    help="'worker' or 'task' -- used in the cascade failure message")
    p.add_argument("--root-run-id", required=True)
    p.add_argument("--root-name", required=True)
    p.add_argument("--parent-run-id", default=None)
    p.add_argument("--parent-name", default=None)
    p.add_argument("--child-status", action="append", default=[],
                    help="path to a child's status JSON output; repeatable, one per child")
    p.add_argument("--status-out", required=True,
                    help="path to write this node's own status JSON for its parent's finalize step")
    return p.parse_args()


def main():
    args = parse_args()
    common.configure()

    resource = "controller_finalize" if args.job_type == "JOB" else "worker_finalize"
    with common.traced_step(resource, run_id=args.run_id, name=args.name):
        child_statuses = [common.read_status(path) for path in args.child_status]
        any_child_failed = any(s["status"] == "FAIL" for s in child_statuses)

        will_fail = common.decide_failure(
            force_fail=args.force_fail, failure_rate=args.failure_rate,
            any_child_failed=any_child_failed, fail_on_child_fail=args.fail_on_child_fail,
        )

        run_facets = common.build_run_facets(
            parent_run_id=args.parent_run_id, parent_name=args.parent_name,
            root_run_id=args.root_run_id, root_name=args.root_name,
            namespace=args.namespace,
        )
        status, error_message = common.emit_terminal(
            namespace=args.namespace, name=args.name, run_id=args.run_id,
            job_type=args.job_type, ol_service=common.ol_service_name(args.job_type),
            run_facets=run_facets, will_fail=will_fail, any_child_failed=any_child_failed,
            fail_on_child_fail=args.fail_on_child_fail, role_label=args.role_label,
        )
        common.write_status(args.status_out, status, error_message)


if __name__ == "__main__":
    main()
