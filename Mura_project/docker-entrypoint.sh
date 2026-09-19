#!/bin/sh
# API and worker in one container, deliberately.
#
# The API writes uploaded audio to a local directory and the worker reads it
# back. Split across two Railway services those are two different filesystems
# and the handoff simply fails, because a Railway volume mounts to exactly one
# service. Until audio lives in object storage, sharing a container is the only
# arrangement in which the pipeline actually runs.
#
# They remain separate processes. The API still never claims or executes a job:
# that rule is about which process does the work, not which host it runs on.
set -e

mkdir -p "${AUDIO_STORAGE_DIR:-/app/data/audio}"

# Alembic owns the schema, and only one process may migrate.
alembic upgrade head

# The worker is supervised, because an unsupervised one is a silent outage.
#
# It died once in production after processing a recording, and nothing brought
# it back: uploads kept succeeding, jobs kept queueing, and every recording sat
# at `attempts: 0` forever while the API went on answering 200. A pipeline that
# stops without saying so is worse than one that fails loudly.
#
# Restarting is safe precisely because of the lease. A job interrupted mid-flight
# is not lost and not marked failed — the lease expires and the next worker
# reclaims it. That guarantee is what makes "just start it again" correct here
# rather than reckless.
# The worker is supervised, and the supervisor must survive the worker.
#
# `set -e` above is inherited by this function. Without the `set +e` below, the
# first non-zero exit — a crash, a signal, anything — terminated the supervisor
# loop itself, so a worker that failed once was never started again. Every
# "hung" worker in production was this: not a hang, a dead supervisor. Uploads
# kept succeeding and jobs sat at `attempts: 0` because nothing was left to
# claim them.
#
# Restarting is safe because of the lease: a job interrupted mid-flight is never
# marked failed, it expires and is reclaimed.
supervise_worker() {
  set +e
  while true; do
    mura-worker
    status=$?
    echo "mura-worker exited status=${status}; restarting in 3s" >&2
    sleep 3
  done
}

supervise_worker &
SUPERVISOR_PID=$!

# Stopping the container must stop both, or Railway waits out the grace period
# on every deploy.
trap 'kill -TERM "$SUPERVISOR_PID" 2>/dev/null' TERM INT

exec uvicorn apps.api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
