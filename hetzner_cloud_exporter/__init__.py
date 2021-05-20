import os
import yaml
import prometheus_client
from pprint import pprint
import requests
import sys
from collections import Counter
import time

CONFIG_PATH = os.getenv(
    "HC_EXPORTER_CONFIG_PATH", "/etc/hetzner_cloud_exporter/targets.yaml"
)
INTERVAL = os.getenv("HC_INTERVAL_SEC", 15)
PORT = os.getenv("HC_PORT", 9303)

hetzner_cloud_load_balancer_target_states_gauge = prometheus_client.Gauge(
    "hetzner_cloud_load_balancer_states",
    "desc",
    ["state", "listen_port", "project_name", "balancer_name"],
)
hetzner_cloud_load_balancer_target_count_gauge = prometheus_client.Gauge(
    "hetzner_cloud_load_balancer_count", "desc", ["project_name", "balancer_name"]
)

hetzner_cloud_load_balancer_not_existent_gauge = prometheus_client.Gauge(
    "hetzner_cloud_load_balancer_not_existent",
    "desc",
    ["project_name", "balancer_name"],
)


def get_all_loadbalancers(token):
    base_url = "https://api.hetzner.cloud/v1"
    path = "/load_balancers"

    response = requests.get(
        "{}{}".format(base_url, path),
        headers={"Authorization": "Bearer {}".format(token)},
    )

    return response.json()


def run():
    prometheus_client.start_http_server(PORT)
    while True:
        start_time = time.time()
        with open(CONFIG_PATH, "r") as fp:
            config = yaml.load(fp, Loader=yaml.Loader)

        for project_name, project in config["targets"].items():
            loadbalancers = get_all_loadbalancers(project["api_key"])

            project["loadbalancer"] = (
                [] if project["loadbalancers"] is None else project["loadbalancers"]
            )
            for project_loadbalancer_name in project["loadbalancer"]:
                try:
                    loadbalancer = [
                        lb
                        for lb in loadbalancers["load_balancers"]
                        if lb["name"] == project_loadbalancer_name
                    ][0]
                except:
                    print(
                        "Loadbalancer {} doesn't exist in project {}.".format(project_loadbalancer_name, project_name)
                    )
                    hetzner_cloud_load_balancer_not_existent_gauge.labels(
                        project_name=project_name,
                        balancer_name=project_loadbalancer_name,
                    ).set(1)

                hetzner_cloud_load_balancer_target_count_gauge.labels(
                    project_name=project_name,
                    balancer_name=loadbalancer["name"],
                ).set(len(loadbalancer["targets"]))

                states = {}
                for target in loadbalancer["targets"]:
                    for health_status in target["health_status"]:
                        states[health_status["listen_port"]] = (
                            []
                            if states.get(health_status["listen_port"]) is None
                            else states[health_status["listen_port"]]
                        )

                        states[health_status["listen_port"]].append(
                            health_status["status"]
                        )

                for port, health_status_list in states.items():
                    states[port] = Counter(health_status_list)

                    for status, count in states[port].items():
                        hetzner_cloud_load_balancer_target_states_gauge.labels(
                            project_name=project_name,
                            balancer_name=loadbalancer["name"],
                            state=status,
                            listen_port=port,
                        ).set(count)

            while start_time + INTERVAL > time.time():
                time.sleep(1)


if __name__ == "__main__":
    run()
