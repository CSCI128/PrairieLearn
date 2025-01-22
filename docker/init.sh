#!/bin/bash

set -e

echo 'Starting PrairieLearn...'

cd /PrairieLearn

make start-support

if [[ $NODEMON == "true" || $DEV == "true" ]]; then
    # make migrate-dev
    make dev-all
else
    make migrate > /dev/null
    make start-all
fi
