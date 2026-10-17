# Overlay the reviewed files onto the exact deployed scheduler image. The caller
# must bind BASE_IMAGE to that local image and preserve the existing service config.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision=${SOURCE_REVISION}
COPY src/jebao_flow/local_control/maintenance.py /usr/local/lib/python3.12/site-packages/jebao_flow/local_control/maintenance.py
COPY src/jebao_flow/local_control/coordinator.py /usr/local/lib/python3.12/site-packages/jebao_flow/local_control/coordinator.py
COPY src/jebao_flow/local_control/external_manual_runtime.py /usr/local/lib/python3.12/site-packages/jebao_flow/local_control/external_manual_runtime.py
COPY src/jebao_flow/local_control_external_manual_cli.py /usr/local/lib/python3.12/site-packages/jebao_flow/local_control_external_manual_cli.py
