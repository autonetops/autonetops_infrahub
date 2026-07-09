# telegraf + the CLI-scrape toolchain for gNMI-less platforms.
# The exec input a gNMI-less platform compiles to runs a netmiko-based
# script; stock telegraf images carry no python, hence this thin extension.
# Nothing compiles to that collector while both PEs are cEOS, but the
# toolchain stays so scoping a signal at the Cisco CEs just works.
FROM telegraf:1.39

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && pip3 install --no-cache-dir --break-system-packages netmiko \
    && rm -rf /var/lib/apt/lists/*
