# telegraf + the CLI-scrape toolchain for gNMI-less platforms.
# The exec input compiled for the Cisco PE runs a netmiko-based script;
# stock telegraf images carry no python, hence this thin extension.
FROM telegraf:1.39

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && pip3 install --no-cache-dir --break-system-packages netmiko \
    && rm -rf /var/lib/apt/lists/*
