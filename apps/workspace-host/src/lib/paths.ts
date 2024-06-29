import * as path from 'node:path';

export const REPOSITORY_ROOT_PATH = path.resolve(import.meta.dirname, '..', '..', '..', '..');

export const APP_ROOT_PATH = path.resolve(import.meta.dirname, '..', '..');


// I want to be able to define an ENV var where we should look for the config file
// so that we can support docker secrets without having to use docker plugins

export const CONFIG_FILE_PATH = process.env.CONFIG_FILE_PATH;
