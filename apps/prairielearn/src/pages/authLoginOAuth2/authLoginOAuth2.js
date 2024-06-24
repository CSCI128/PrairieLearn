// @ts-check
import { Router } from 'express';
import asyncHandler from 'express-async-handler';
// Need to change to MSFT oauth library
// I think this one?? https://github.com/AzureAD/microsoft-authentication-library-for-js
import { OAuth2Client } from 'google-auth-library';

import { HttpStatusError } from '@prairielearn/error';

import { config } from '../../lib/config.js';

const router = Router();

/**
  * Need to update to use EntraID stuff
  * Should be pretty similar - it seems to just be yoiking from the config file
  * */
router.get(
  '/',
  asyncHandler(async (req, res) => {
    if (
      !config.hasOauth ||
      !config.googleClientId ||
      !config.googleClientSecret ||
      !config.googleRedirectUrl
    ) {
      throw new HttpStatusError(404, 'Google login is not enabled');
    }

    const oauth2Client = new OAuth2Client(
      config.googleClientId,
      config.googleClientSecret,
      config.googleRedirectUrl,
    );
    const url = oauth2Client.generateAuthUrl({
      access_type: 'online',
      // are these scopes enough??
      scope: ['openid', 'profile', 'email'],
      prompt: 'select_account',
      // FIXME: should add some state here to avoid CSRF
    });
    res.redirect(url);
  }),
);

export default router;
