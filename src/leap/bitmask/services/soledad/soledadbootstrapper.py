# -*- coding: utf-8 -*-
# soledadbootstrapper.py
# Copyright (C) 2013 LEAP
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
Soledad bootstrapping
"""
import logging
import os
import socket

from ssl import SSLError

from PySide import QtCore
from u1db import errors as u1db_errors

from leap.bitmask.config import flags
from leap.bitmask.config.providerconfig import ProviderConfig
from leap.bitmask.crypto.srpauth import SRPAuth
from leap.bitmask.services.abstractbootstrapper import AbstractBootstrapper
from leap.bitmask.services.soledad.soledadconfig import SoledadConfig
from leap.bitmask.util.request_helpers import get_content
from leap.bitmask.util import is_file, is_empty_file
from leap.bitmask.util import get_path_prefix
from leap.common.files import get_mtime
from leap.common.check import leap_assert, leap_assert_type, leap_check
from leap.keymanager import KeyManager, openpgp
from leap.keymanager.errors import KeyNotFound
from leap.soledad.client import Soledad

logger = logging.getLogger(__name__)


# TODO these exceptions could be moved to soledad itself
# after settling this down.

class SoledadSyncError(Exception):
    message = "Error while syncing Soledad"


class SoledadInitError(Exception):
    message = "Error while initializing Soledad"


class SoledadBootstrapper(AbstractBootstrapper):
    """
    Soledad init procedure
    """

    SOLEDAD_KEY = "soledad"
    KEYMANAGER_KEY = "keymanager"

    PUBKEY_KEY = "user[public_key]"

    MAX_INIT_RETRIES = 10

    # All dicts returned are of the form
    # {"passed": bool, "error": str}
    download_config = QtCore.Signal(dict)
    gen_key = QtCore.Signal(dict)
    soledad_timeout = QtCore.Signal()
    soledad_failed = QtCore.Signal()

    def __init__(self):
        AbstractBootstrapper.__init__(self)

        self._provider_config = None
        self._soledad_config = None
        self._keymanager = None
        self._download_if_needed = False
        self._user = ""
        self._password = ""
        self._srpauth = None
        self._soledad = None

        self._soledad_retries = 0

    @property
    def keymanager(self):
        return self._keymanager

    @property
    def soledad(self):
        return self._soledad

    @property
    def srpauth(self):
        leap_assert(self._provider_config is not None,
                    "We need a provider config")
        return SRPAuth(self._provider_config)

    # retries

    def cancel_bootstrap(self):
        self._soledad_retries = self.MAX_INIT_RETRIES

    def should_retry_initialization(self):
        """
        Returns True if we should retry the initialization.
        """
        logger.debug("current retries: %s, max retries: %s" % (
            self._soledad_retries,
            self.MAX_INIT_RETRIES))
        return self._soledad_retries < self.MAX_INIT_RETRIES

    def increment_retries_count(self):
        """
        Increments the count of initialization retries.
        """
        self._soledad_retries += 1

    def _get_db_paths(self, uuid):
        """
        Returns the secrets and local db paths needed for soledad
        initialization

        :param uuid: uuid for user
        :type uuid: str

        :return: a tuple with secrets, local_db paths
        :rtype: tuple
        """
        prefix = os.path.join(get_path_prefix(), "leap", "soledad")
        secrets = "%s/%s.secret" % (prefix, uuid)
        local_db = "%s/%s.db" % (prefix, uuid)

        # We remove an empty file if found to avoid complains
        # about the db not being properly initialized
        if is_file(local_db) and is_empty_file(local_db):
            try:
                os.remove(local_db)
            except OSError:
                logger.warning("Could not remove empty file %s"
                               % local_db)
        return secrets, local_db

    # initialization

    def load_and_sync_soledad(self):
        """
        Once everthing is in the right place, we instantiate and sync
        Soledad
        """
        uuid = self.srpauth.get_uid()
        token = self.srpauth.get_token()

        secrets_path, local_db_path = self._get_db_paths(uuid)

        # TODO: Select server based on timezone (issue #3308)
        server_dict = self._soledad_config.get_hosts()

        if not server_dict.keys():
            # XXX raise more specific exception
            raise Exception("No soledad server found")

        selected_server = server_dict[server_dict.keys()[0]]
        server_url = "https://%s:%s/user-%s" % (
            selected_server["hostname"],
            selected_server["port"],
            uuid)

        logger.debug("Using soledad server url: %s" % (server_url,))

        cert_file = self._provider_config.get_ca_cert_path()

        logger.debug('local_db:%s' % (local_db_path,))
        logger.debug('secrets_path:%s' % (secrets_path,))

        try:
            self._try_soledad_init(
                uuid, secrets_path, local_db_path,
                server_url, cert_file, token)
        except:
            # re-raise the exceptions from try_init,
            # we're currently handling the retries from the
            # soledad-launcher in the gui.
            raise

        leap_check(self._soledad is not None,
                   "Null soledad, error while initializing")

        # and now, let's sync
        sync_tries = 10
        while sync_tries > 0:
            try:
                self._try_soledad_sync()
                logger.debug("Soledad has been synced.")
                # so long, and thanks for all the fish
                return
            except SoledadSyncError:
                # maybe it's my connection, but I'm getting
                # ssl handshake timeouts and read errors quite often.
                # A particularly big sync is a disaster.
                # This deserves further investigation, maybe the
                # retry strategy can be pushed to u1db, or at least
                # it's something worthy to talk about with the
                # ubuntu folks.
                sync_tries -= 1
                continue

        # reached bottom, failed to sync
        # and there's nothing we can do...
        self.soledad_failed.emit()
        raise SoledadSyncError()

    def _try_soledad_init(self, uuid, secrets_path, local_db_path,
                          server_url, cert_file, auth_token):
        """
        Tries to initialize soledad.

        :param uuid: user identifier
        :param secrets_path: path to secrets file
        :param local_db_path: path to local db file
        :param server_url: soledad server uri
        :cert_file:
        :param auth token: auth token
        :type auth_token: str
        """

        # TODO: If selected server fails, retry with another host
        # (issue #3309)
        try:
            self._soledad = Soledad(
                uuid,
                self._password.encode("utf-8"),
                secrets_path=secrets_path,
                local_db_path=local_db_path,
                server_url=server_url,
                cert_file=cert_file,
                auth_token=auth_token)

        # XXX All these errors should be handled by soledad itself,
        # and return a subclass of SoledadInitializationFailed
        except socket.timeout:
            logger.debug("SOLEDAD initialization TIMED OUT...")
            self.soledad_timeout.emit()
        except socket.error as exc:
            logger.error("Socket error while initializing soledad")
            self.soledad_failed.emit()
        except u1db_errors.Unauthorized:
            logger.error("Error while initializing soledad "
                         "(unauthorized).")
            self.soledad_failed.emit()
        except Exception as exc:
            logger.exception("Unhandled error while initializating "
                             "soledad: %r" % (exc,))
            self.soledad_failed.emit()

    def _try_soledad_sync(self):
        """
        Tries to sync soledad.
        Raises SoledadSyncError if not successful.
        """
        try:
            logger.error("trying to sync soledad....")
            self._soledad.sync()
        except SSLError as exc:
            logger.error("%r" % (exc,))
            raise SoledadSyncError("Failed to sync soledad")
        except Exception as exc:
            logger.exception("Unhandled error while syncing"
                             "soledad: %r" % (exc,))
            self.soledad_failed.emit()
            raise SoledadSyncError("Failed to sync soledad")

    def _download_config(self):
        """
        Downloads the Soledad config for the given provider
        """

        leap_assert(self._provider_config,
                    "We need a provider configuration!")

        logger.debug("Downloading Soledad config for %s" %
                     (self._provider_config.get_domain(),))

        self._soledad_config = SoledadConfig()

        # TODO factor out with eip/provider configs.

        headers = {}
        mtime = get_mtime(
            os.path.join(get_path_prefix(), "leap", "providers",
                         self._provider_config.get_domain(),
                         "soledad-service.json"))

        if self._download_if_needed and mtime:
            headers['if-modified-since'] = mtime

        api_version = self._provider_config.get_api_version()

        # there is some confusion with this uri,
        config_uri = "%s/%s/config/soledad-service.json" % (
            self._provider_config.get_api_uri(),
            api_version)
        logger.debug('Downloading soledad config from: %s' % config_uri)

        # TODO factor out this srpauth protected get (make decorator)
        srp_auth = self.srpauth
        session_id = srp_auth.get_session_id()
        cookies = None
        if session_id:
            cookies = {"_session_id": session_id}

        res = self._session.get(config_uri,
                                verify=self._provider_config
                                .get_ca_cert_path(),
                                headers=headers,
                                cookies=cookies)
        res.raise_for_status()

        self._soledad_config.set_api_version(api_version)

        # Not modified
        if res.status_code == 304:
            logger.debug("Soledad definition has not been modified")
            self._soledad_config.load(
                os.path.join(
                    "leap", "providers",
                    self._provider_config.get_domain(),
                    "soledad-service.json"))
        else:
            soledad_definition, mtime = get_content(res)

            self._soledad_config.load(data=soledad_definition, mtime=mtime)
            self._soledad_config.save(["leap",
                                       "providers",
                                       self._provider_config.get_domain(),
                                       "soledad-service.json"])

        self.load_and_sync_soledad()

    def _gen_key(self, _):
        """
        Generates the key pair if needed, uploads it to the webapp and
        nickserver
        """
        leap_assert(self._provider_config is not None,
                    "We need a provider configuration!")
        leap_assert(self._soledad is not None,
                    "We need a non-null soledad to generate keys")

        address = "%s@%s" % (self._user, self._provider_config.get_domain())

        logger.debug("Retrieving key for %s" % (address,))

        srp_auth = self.srpauth

        # TODO: use which implementation with known paths
        # TODO: Fix for Windows
        gpgbin = "/usr/bin/gpg"

        if flags.STANDALONE:
            gpgbin = os.path.join(get_path_prefix(),
                                  "..", "apps", "mail", "gpg")

        self._keymanager = KeyManager(
            address,
            "https://nicknym.%s:6425" % (self._provider_config.get_domain(),),
            self._soledad,
            #token=srp_auth.get_token(),  # TODO: enable token usage
            session_id=srp_auth.get_session_id(),
            ca_cert_path=self._provider_config.get_ca_cert_path(),
            api_uri=self._provider_config.get_api_uri(),
            api_version=self._provider_config.get_api_version(),
            uid=srp_auth.get_uid(),
            gpgbinary=gpgbin)
        try:
            self._keymanager.get_key(address, openpgp.OpenPGPKey,
                                     private=True, fetch_remote=False)
        except KeyNotFound:
            logger.debug("Key not found. Generating key for %s" % (address,))
            self._keymanager.gen_key(openpgp.OpenPGPKey)
            self._keymanager.send_key(openpgp.OpenPGPKey)
            logger.debug("Key generated successfully.")

    def run_soledad_setup_checks(self,
                                 provider_config,
                                 user,
                                 password,
                                 download_if_needed=False):
        """
        Starts the checks needed for a new soledad setup

        :param provider_config: Provider configuration
        :type provider_config: ProviderConfig
        :param user: User's login
        :type user: str
        :param password: User's password
        :type password: str
        :param download_if_needed: If True, it will only download
                                   files if the have changed since the
                                   time it was previously downloaded.
        :type download_if_needed: bool
        """
        leap_assert_type(provider_config, ProviderConfig)

        # XXX we should provider a method for setting provider_config
        self._provider_config = provider_config
        self._download_if_needed = download_if_needed
        self._user = user
        self._password = password

        cb_chain = [
            (self._download_config, self.download_config),
            (self._gen_key, self.gen_key)
        ]

        self.addCallbackChain(cb_chain)
