#!/usr/bin/env python3
"""Fetch compiled artifacts from InfraHub into the places the lab
consumes them.

This (plus deploy_configs.py) is the stand-in for the orchestration /
execution layer, which is deliberately out of scope. A real runner would
do exactly this on merge events; here you run it by hand and watch each
piece land:

    device-configuration  -> build/configs/<device>.cfg
    telegraf-inputs       -> monitoring/telegraf/telegraf.d/<device>.conf
    prometheus-rules      -> monitoring/prometheus/rules/<contract>.yml
    grafana-dashboard     -> monitoring/grafana/dashboards/<tenant>.json
    contract-expectations -> build/expectations/<contract>.yml

Environment: INFRAHUB_ADDRESS, INFRAHUB_API_TOKEN, INFRAHUB_BRANCH.
"""

import os
import pathlib
import sys

import httpx

ADDRESS = os.environ.get("INFRAHUB_ADDRESS", "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("INFRAHUB_API_TOKEN", "")
BRANCH = os.environ.get("INFRAHUB_BRANCH", "main")

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

DESTINATIONS = {
    "device-configuration": (REPO_ROOT / "build" / "configs", "cfg"),
    "telegraf-inputs": (REPO_ROOT / "monitoring" / "telegraf" / "telegraf.d", "conf"),
    "prometheus-rules": (REPO_ROOT / "monitoring" / "prometheus" / "rules", "yml"),
    "grafana-dashboard": (REPO_ROOT / "monitoring" / "grafana" / "dashboards", "json"),
    "contract-expectations": (REPO_ROOT / "build" / "expectations", "yml"),
}

QUERY = """
query {
  CoreArtifact {
    edges {
      node {
        name { value }
        storage_id { value }
        object { node { display_label } }
        definition { node { artifact_name { value } } }
      }
    }
  }
}
"""


def main():
    headers = {"X-INFRAHUB-KEY": TOKEN} if TOKEN else {}
    with httpx.Client(base_url=ADDRESS, headers=headers, timeout=30) as http:
        response = http.post(
            f"/graphql/{BRANCH}", json={"query": QUERY}
        )
        response.raise_for_status()
        artifacts = response.json()["data"]["CoreArtifact"]["edges"]

        if not artifacts:
            print("no artifacts found - generate them (merge a Proposed Change "
                  "or run the artifact definitions) first")
            sys.exit(1)

        for edge in artifacts:
            node = edge["node"]
            definition = node["definition"]["node"]["artifact_name"]["value"]
            if definition not in DESTINATIONS:
                continue
            directory, extension = DESTINATIONS[definition]
            directory.mkdir(parents=True, exist_ok=True)

            target = node["object"]["node"]["display_label"]
            storage_id = node["storage_id"]["value"]
            content = http.get(f"/api/storage/object/{storage_id}")
            content.raise_for_status()

            path = directory / f"{target}.{extension}"
            path.write_text(content.text)
            print(f"  {definition:22s} {target:20s} -> {path.relative_to(REPO_ROOT)}")

    print("done. Restart/reload telegraf, prometheus and grafana to pick up "
          "the new compiled state.")


if __name__ == "__main__":
    main()
