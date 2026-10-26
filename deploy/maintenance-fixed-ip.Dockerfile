ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY --chmod=0644 src/jebao_flow/local_control/fixed_endpoint.py /usr/local/lib/python3.12/site-packages/jebao_flow/local_control/fixed_endpoint.py
COPY --chmod=0644 src/jebao_flow/local_control_external_manual_cli.py /usr/local/lib/python3.12/site-packages/jebao_flow/local_control_external_manual_cli.py
