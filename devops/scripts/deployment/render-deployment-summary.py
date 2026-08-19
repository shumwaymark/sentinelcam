#!/usr/bin/env python3
"""Fold the deployment summary records into one table.

Reads the JSON lines written by the `sentinelcam_summary` callback -- one record per
playbook run -- and prints a short verdict. The full task detail stays in the
deployment log; this answers the only questions worth asking at a glance: did anything
fail, and what got restarted.

Exit status is 1 when any host failed or was unreachable, so a caller can branch on it.

Usage: render-deployment-summary.py [path/to/deployment_summary.jsonl]
"""

import json
import os
import sys

RULE = "-" * 68


def load(path):
    records = []
    try:
        with open(path) as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except ValueError:
                        continue          # a torn write is not worth failing over
    except FileNotFoundError:
        return None
    return records


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        'logs', 'deployment_summary.jsonl')
    records = load(path)
    if records is None:
        print(f"No deployment summary at {path}")
        return 0
    if not records:
        print("Deployment summary is empty -- no playbooks recorded a result.")
        return 0

    rows, bad, hosts_seen = [], 0, 0
    for record in records:
        component = record.get('component', '?')
        first = True
        for host, stat in sorted(record.get('hosts', {}).items()):
            hosts_seen += 1
            failed = stat.get('failures', 0) + stat.get('unreachable', 0)
            if failed:
                bad += 1
                if stat.get('unreachable'):
                    verdict = "UNREACHABLE"
                else:
                    verdict = f"FAILED ({failed})"
            elif stat.get('changed'):
                verdict = f"{stat['changed']} changed"
            else:
                verdict = "no change"
            restarted = stat.get('restarted') or []
            note = "restarted: " + ", ".join(restarted) if restarted else ""
            rows.append((component if first else "", host, verdict, note,
                         stat.get('errors') or []))
            first = False

    width = max((len(r[0]) for r in rows), default=9)
    print(RULE)
    print("DEPLOYMENT SUMMARY")
    print(RULE)
    for component, host, verdict, note, errors in rows:
        print(f"{component:<{width}}  {host:<9} {verdict:<13} {note}")
        for error in errors:
            print(f"{'':<{width}}  {'':<9} -> {error.get('task', '?')}")
            print(f"{'':<{width}}  {'':<9}    {error.get('reason', '')}")
    print(RULE)
    verdict = "ALL OK" if not bad else f"{bad} HOST(S) FAILED"
    print(f"{len(records)} component(s) - {hosts_seen} host(s) - {verdict}")
    print(RULE)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
