import os
import shutil
import socket

from twisted.internet import reactor
from twisted.logger import Logger

import psutil
try:
    # psutil < 2.0.0
    from psutil.error import AccessDenied as psutil_AccessDenied
    PSUTIL_2 = False
except ImportError:
    # psutil >= 2.0.0
    from psutil import AccessDenied as psutil_AccessDenied
    PSUTIL_2 = True

from leap.bitmask.vpn._telnet import UDSTelnet


class OpenVPNAlreadyRunning(Exception):
    message = ("Another openvpn instance is already running, and could "
               "not be stopped.")


class AlienOpenVPNAlreadyRunning(Exception):
    message = ("Another openvpn instance is already running, and could "
               "not be stopped because it was not launched by LEAP.")


class ImproperlyConfigured(Exception):
    pass


class VPNManagement(object):
    """
    A class to handle the communication with the openvpn management
    interface.

    For more info about management methods::

      zcat `dpkg -L openvpn | grep management`
    """
    log = Logger()

    # Timers, in secs
    CONNECTION_RETRY_TIME = 1

    def __init__(self):
        self._tn = None
        self.aborted = False
        self._host = None
        self._port = None

        self._watcher = None
        self._logs = {}

    def set_connection(self, host, port):
        """
        :param host: either socket path (unix) or socket IP
        :type host: str

        :param port: either string "unix" if it's a unix socket, or port
                     otherwise
        """
        self._host = host
        self._port = port

    def set_watcher(self, watcher):
        self._watcher = watcher

    def is_connected(self):
        return bool(self._tn)

    def connect_retry(self, retry=0, max_retries=None):
        """
        Attempts to connect to a management interface, and retries
        after CONNECTION_RETRY_TIME if not successful.

        :param retry: number of the retry
        :type retry: int
        """
        if max_retries and retry > max_retries:
            self.log.warn(
                'Max retries reached while attempting to connect '
                'to management. Aborting.')
            self.aborted = True
            return

        if not self.aborted and not self.is_connected():
            self._connect()
            reactor.callLater(
                self.CONNECTION_RETRY_TIME,
                self.connect_retry, retry + 1, max_retries)

    def _connect(self):
        if not self._host or not self._port:
            raise ImproperlyConfigured('Connection is not configured')

        try:
            self._tn = UDSTelnet(self._host, self._port)
            self._tn.read_eager()

        except Exception as e:
            self.log.warn('Could not connect to OpenVPN yet: %r' % (e,))
            self._tn = None

        if self._tn:
            return True
        else:
            self.log.error('Error while connecting to management!')
            return False

    def process_log(self):
        if not self._watcher or not self._tn:
            return

        lines = self._send_command('log 20')
        for line in lines:
            try:
                splitted = line.split(',')
                ts = splitted[0]
                msg = ','.join(splitted[2:])
                if msg.startswith('MANAGEMENT'):
                    continue
                if ts not in self._logs:
                    self._watcher.watch(msg)
                    self.log.info('VPN: %s' % msg)
                    self._logs[ts] = msg
            except Exception:
                pass

    def _seek_to_eof(self):
        """
        Read as much as available. Position seek pointer to end of stream
        """
        try:
            return self._tn.read_eager()
        except EOFError:
            self.log.debug('Could not read from socket. Assuming it died.')
            return

    def _send_command(self, command, until=b"END"):
        """
        Sends a command to the telnet connection and reads until END
        is reached.

        :param command: command to send
        :type command: str

        :param until: byte delimiter string for reading command output
        :type until: byte str

        :return: response read
        :rtype: list
        """
        try:
            self._tn.write("%s\n" % (command,))
            buf = self._tn.read_until(until)
            seek = self._seek_to_eof()
            blist = buf.split('\r\n')
            if blist[-1].startswith(until):
                del blist[-1]
                return blist
            else:
                return []

        except socket.error:
            # XXX should get a counter and repeat only
            # after mod X times.
            self.log.warn('Socket error (command was: "%s")' % (command,))
            self._close_management_socket(announce=False)
            self.log.debug('Trying to connect to management again')
            self.connect_retry(max_retries=5)
            return []

        except Exception as e:
            self.log.warn("Error sending command %s: %r" %
                          (command, e))
        return []

    def _close_management_socket(self, announce=True):
        """
        Close connection to openvpn management interface.
        """
        if announce:
            self._tn.write("quit\n")
            self._tn.read_all()
        self._tn.get_socket().close()
        self._tn = None

    def _parse_state(self, output):
        """
        Parses the output of the state command.

        :param output: list of lines that the state command printed as
                       its output
        :type output: list
        """
        for line in output:
            status_step = ''
            stripped = line.strip()
            if stripped == "END":
                continue
            parts = stripped.split(",")
            if len(parts) < 5:
                continue
            try:
                ts, status_step, ok, ip, remote, port, _, _, _ = parts
            except ValueError:
                try:
                    ts, status_step, ok, ip, remote, port, _, _ = parts
                except ValueError:
                    self.log.debug('Could not parse state line: %s' % line)

            return status_step

    def _parse_status(self, output):
        """
        Parses the output of the status command.

        :param output: list of lines that the status command printed
                       as its output
        :type output: list
        """
        tun_tap_read = ""
        tun_tap_write = ""

        for line in output:
            stripped = line.strip()
            if stripped.endswith("STATISTICS") or stripped == "END":
                continue
            parts = stripped.split(",")
            if len(parts) < 2:
                continue

            try:
                text, value = parts
            except ValueError:
                self.log.debug('Could not parse status line %s' % line)
                return
            # text can be:
            #   "TUN/TAP read bytes"
            #   "TUN/TAP write bytes"
            #   "TCP/UDP read bytes"
            #   "TCP/UDP write bytes"
            #   "Auth read bytes"

            if text == "TUN/TAP read bytes":
                tun_tap_read = value  # download
            elif text == "TUN/TAP write bytes":
                tun_tap_write = value  # upload

        return (tun_tap_read, tun_tap_write)

    def get_state(self):
        if not self.is_connected():
            return ""
        state = self._parse_state(self._send_command("state"))
        return state

    def get_traffic_status(self):
        if not self.is_connected():
            return (None, None)
        return self._parse_status(self._send_command("status"))

    def terminate(self, shutdown=False):
        """
        Attempts to terminate openvpn by sending a SIGTERM.
        """
        if self.is_connected():
            self._send_command("signal SIGTERM")
        if shutdown:
            self._cleanup_tempfiles()

    def _cleanup_tempfiles(self):
        """
        Remove all temporal files we might have left behind.

        Iif self.port is 'unix', we have created a temporal socket path that,
        under normal circumstances, we should be able to delete.
        """
        if self._socket_port == "unix":
            tempfolder = _first(os.path.split(self._host))
            if tempfolder and os.path.isdir(tempfolder):
                try:
                    shutil.rmtree(tempfolder)
                except OSError:
                    self.log.error(
                        'Could not delete tmpfolder %s' % tempfolder)

    def get_openvpn_process(self):
        """
        Looks for openvpn instances running.

        :rtype: process
        """
        openvpn_process = None
        for p in psutil.process_iter():
            try:
                # XXX Not exact!
                # Will give false positives.
                # we should check that cmdline BEGINS
                # with openvpn or with our wrapper
                # (pkexec / osascript / whatever)

                # This needs more work, see #3268, but for the moment
                # we need to be able to filter out arguments in the form
                # --openvpn-foo, since otherwise we are shooting ourselves
                # in the feet.

                if PSUTIL_2:
                    cmdline = p.cmdline()
                else:
                    cmdline = p.cmdline
                if any(map(lambda s: s.find(
                        "LEAPOPENVPN") != -1, cmdline)):
                    openvpn_process = p
                    break
            except psutil_AccessDenied:
                pass
        return openvpn_process

    def stop_if_already_running(self):
        """
        Checks if VPN is already running and tries to stop it.

        Might raise OpenVPNAlreadyRunning.

        :return: True if stopped, False otherwise

        """
        process = self.get_openvpn_process()
        if not process:
            self.log.debug('Could not find openvpn process while '
                           'trying to stop it.')
            return

        self.log.debug('OpenVPN is already running, trying to stop it...')
        cmdline = process.cmdline

        manag_flag = "--management"

        if isinstance(cmdline, list) and manag_flag in cmdline:

            # we know that our invocation has this distinctive fragment, so
            # we use this fingerprint to tell other invocations apart.
            # this might break if we change the configuration path in the
            # launchers

            def smellslikeleap(s):
                return "leap" in s and "providers" in s

            if not any(map(smellslikeleap, cmdline)):
                self.log.debug("We cannot stop this instance since we do not "
                               "recognise it as a leap invocation.")
                raise AlienOpenVPNAlreadyRunning

            try:
                index = cmdline.index(manag_flag)
                host = cmdline[index + 1]
                port = cmdline[index + 2]
                self.log.debug("Trying to connect to %s:%s"
                               % (host, port))
                self._connect()

                # XXX this has a problem with connections to different
                # remotes. So the reconnection will only work when we are
                # terminating instances left running for the same provider.
                # If we are killing an openvpn instance configured for another
                # provider, we will get:
                # TLS Error: local/remote TLS keys are out of sync
                # However, that should be a rare case right now.
                self._send_command("signal SIGTERM")
                self._close_management_socket(announce=True)
            except (Exception, AssertionError):
                self.log.failure('Problem trying to terminate OpenVPN')
        else:
            self.log.debug('Could not find the expected openvpn command line.')

        process = self.get_openvpn_process()
        if process is None:
            self.log.debug('Successfully finished already running '
                           'openvpn process.')
            return True
        else:
            self.log.warn('Unable to terminate OpenVPN')
            raise OpenVPNAlreadyRunning


def _first(things):
    try:
        return things[0]
    except (IndexError, TypeError):
        return None
