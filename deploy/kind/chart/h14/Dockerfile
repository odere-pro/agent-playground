# The H14 test workload: the echo-python image plus one module that binds a loopback port at
# start (h14_squat.py). Built by deploy/kind/chart/run.sh from this folder, on top of the
# echo-python image it has just built; never pushed, never used outside the kind check.
# The handle is set here, in the image's own entrypoint: the chart allow-lists the workload's
# arguments and refuses `--handle`.
ARG ECHO_IMAGE=kind.local/agent-platform/echo-python:poc09
FROM ${ECHO_IMAGE}
# 0555: --chmod also sets the mode of the new /app/h14 directory, which needs x to be read.
COPY --chown=0:0 --chmod=0555 h14_squat.py /app/h14/h14_squat.py
ENV PYTHONPATH=/app/h14
ENTRYPOINT ["sh", "-c", "exec workload-a2a serve --handle h14_squat:handle --name h14-squat --version 0.1.0 --host \"$HOST\" --port \"$PORT\" \"$@\"", "--"]
