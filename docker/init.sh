#!/bin/bash

set -e

echo 'Starting PrairieLearn...'

cd /PrairieLearn

echo "starting support"
make start-support

if [[ $NODEMON == "true" || $DEV == "true" ]]; then
    echo 'running migration for dev'
    # make migrate-dev
    echo 'migration complete - running dev'
    make dev-all
else
    echo 'running migration for prod'
    make migrate > /dev/null
    make start-all
fi
