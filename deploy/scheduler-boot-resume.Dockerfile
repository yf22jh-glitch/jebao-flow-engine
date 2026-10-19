# Bind BASE_IMAGE to the currently deployed scheduler; preserve its runtime config.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision=${SOURCE_REVISION}
COPY --chmod=0644 src/jebao_flow/local_control/external_manual_runtime.py /usr/local/lib/python3.12/site-packages/jebao_flow/local_control/external_manual_runtime.py
COPY --chmod=0644 src/jebao_flow/local_control/external_manual_editor.py /usr/local/lib/python3.12/site-packages/jebao_flow/local_control/external_manual_editor.py
COPY --chmod=0644 src/jebao_flow/local_control_external_manual_cli.py /usr/local/lib/python3.12/site-packages/jebao_flow/local_control_external_manual_cli.py
