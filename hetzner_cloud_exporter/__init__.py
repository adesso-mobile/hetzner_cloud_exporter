import os
from requests.models import HTTPError
import yaml
import prometheus_client
from pprint import pprint
import requests
import sys
from collections import Counter
import time
import logging


CONFIG_PATH = os.getenv(
    "HC_EXPORTER_CONFIG_PATH", "/etc/hetzner_cloud_exporter/targets.yaml"
)
INTERVAL = os.getenv("HC_EXPORTER_INTERVAL_SEC", 15)
PORT = os.getenv("HC_EXPORTER_PORT", 9303)

LOGLEVEL = os.getenv("HC_EXPORTER_LOGLEVEL", "INFO")
LOGPATH = os.getenv("HC_EXPORTER_LOGPATH", "/var/log/hetzner_cloud_exporter.log")

log = logging.getLogger("Hetzner Cloud Exporter")

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)

log.addHandler(console_handler)
log.setLevel(LOGLEVEL)


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


def _hcloud_get_all_loadbalancers(token):
    base_url = "https://api.hetzner.cloud/v1"
    path = "/load_balancers"

    response = requests.get(
        "{}{}".format(base_url, path),
        headers={"Authorization": "Bearer {}".format(token)},
    )

    response.raise_for_status()

    return response.json()


def _load_config(path):
    with open(CONFIG_PATH, "r") as fp:
        config = yaml.load(fp, Loader=yaml.Loader)

    return config


def _search_loadbalancers_by_name(loadbalancers, project_loadbalancer_name):
    try:
        loadbalancer = [
            lb
            for lb in loadbalancers["load_balancers"]
            if lb["name"] == project_loadbalancer_name
        ][0]
    except IndexError:
        loadbalancer = None

    return loadbalancer


def _aggregate_loadbalancer_states(loadbalancer):
    states = {}
    for target in loadbalancer["targets"]:
        for health_status in target["health_status"]:
            states[health_status["listen_port"]] = (
                []
                if states.get(health_status["listen_port"]) is None
                else states[health_status["listen_port"]]
            )

            states[health_status["listen_port"]].append(health_status["status"])

    return {
        port: Counter(health_status_list) for port, health_status_list in states.items()
    }


def run():
    try:
        log.info("Starting exporter on port {}".format(PORT))
        prometheus_client.start_http_server(PORT)
        while True:
            start_time = time.time()

            config = _load_config(CONFIG_PATH)

            for project_name, project in config["targets"].items():

                try:
                    loadbalancers = _hcloud_get_all_loadbalancers(project["api_key"])
                except HTTPError as e:
                    log.error(
                        "project {}: Error while calling HCloud API: {}".format(
                            project_name, e
                        )
                    )
                    continue

                project["loadbalancer"] = (
                    [] if project["loadbalancers"] is None else project["loadbalancers"]
                )
                for project_loadbalancer_name in project["loadbalancer"]:
                    loadbalancer = _search_loadbalancers_by_name(
                        loadbalancers, project_loadbalancer_name
                    )

                    if loadbalancer is None:
                        log.error(
                            "lb {}:{}: Loadbalancer doesn't exist.".format(
                                project_name, project_loadbalancer_name
                            )
                        )
                        hetzner_cloud_load_balancer_not_existent_gauge.labels(
                            project_name=project_name,
                            balancer_name=project_loadbalancer_name,
                        ).set(1)
                        continue

                    log.info(
                        "lb {}:{}: Found {} targets".format(
                            project_name,
                            project_loadbalancer_name,
                            len(loadbalancer["targets"]),
                        )
                    )
                    hetzner_cloud_load_balancer_target_count_gauge.labels(
                        project_name=project_name,
                        balancer_name=project_loadbalancer_name,
                    ).set(len(loadbalancer["targets"]))

                    log.info(
                        "lb {}:{}: Aggregating states:".format(
                            project_name, project_loadbalancer_name
                        )
                    )
                    states = _aggregate_loadbalancer_states(loadbalancer)
                    log.info(
                        "lb {}:{}: {}".format(
                            project_name, project_loadbalancer_name, states
                        )
                    )

                    for port, health_status_list in states.items():
                        for status, count in states[port].items():
                            hetzner_cloud_load_balancer_target_states_gauge.labels(
                                project_name=project_name,
                                balancer_name=loadbalancer["name"],
                                state=status,
                                listen_port=port,
                            ).set(count)

                while start_time + INTERVAL > time.time():
                    time.sleep(1)
    except:
        log.exception("Unexpected error:")


if __name__ == "__main__":
    run()
