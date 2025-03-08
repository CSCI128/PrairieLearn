# syntax=docker/dockerfile-upstream:master-labs

FROM prairielearn/plbase:latest

ENV PATH="/PrairieLearn/node_modules/.bin:$PATH"

# This copies in all the `package.json` files in `apps` and `packages`, which
# Yarn needs to correctly install all dependencies in our workspaces.
# The `--parents` flag is used to preserve parent directories for the sources.
#
# We also need to copy both the `.yarn` directory and the `.yarnrc.yml` file,
# both of which are necessary for Yarn to correctly install dependencies.
#
# Finally, we copy `packages/bind-mount/` since this package contains native
# code that will be built during the install process.
COPY --parents .yarn/ yarn.lock .yarnrc.yml **/package.json packages/bind-mount/ /PrairieLearn/

# Install Node dependencies.
RUN cd /PrairieLearn && yarn install --immutable && yarn cache clean

# NOTE: Modify .dockerignore to allowlist files/directories to copy.
COPY . /PrairieLearn/

# set up PrairieLearn and run migrations to initialize the DB
RUN chmod +x /PrairieLearn/docker/init.sh \
    && mkdir /course{,{2..9}} \
    && mkdir -p /workspace_{main,host}_zips \
    && mkdir -p /jobs \
    && cd /PrairieLearn \
    && make build \
    && /PrairieLearn/docker/gen_ssl.sh \
    && git config --global user.email "pl@mines.edu" \
    && git config --global user.name "PrairieLearn Server" \
    && git config --global safe.directory '*'


CMD /PrairieLearn/docker/init.sh
