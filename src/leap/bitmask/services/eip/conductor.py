# -*- coding: utf-8 -*-
# conductor.py
# Copyright (C) 2014 LEAP
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
EIP Conductor module.
"""
import logging

from PySide import QtCore

from leap.bitmask.gui import statemachines
from leap.bitmask.services import EIP_SERVICE
from leap.bitmask.services import get_service_display_name
from leap.bitmask.services.eip.connection import EIPConnection
from leap.bitmask.platform_init import IS_MAC
from leap.bitmask.util import make_address

QtDelayedCall = QtCore.QTimer.singleShot
logger = logging.getLogger(__name__)


class EIPConductor(object):

    def __init__(self, settings, backend, **kwargs):
        """
        Initializes EIP Conductor.

        :param settings:
        :type settings:

        :param backend:
        :type backend:
        """
        self.eip_connection = EIPConnection()
        self.eip_name = get_service_display_name(EIP_SERVICE)
        self._settings = settings
        self._backend = backend

        self._eip_status = None

    @property
    def qtsigs(self):
        return self.eip_connection.qtsigs

    def add_eip_widget(self, widget):
        """
        Keep a reference to the passed eip status widget.

        :param widget: the EIP Status widget.
        :type widget: QWidget
        """
        self._eip_status = widget

    def connect_signals(self):
        """
        Connect signals.
        """
        self.qtsigs.connecting_signal.connect(self._start_eip)
        self.qtsigs.disconnecting_signal.connect(self._stop_eip)
        self.qtsigs.disconnected_signal.connect(self._eip_status.eip_stopped)

    def connect_backend_signals(self):
        """
        Connect to backend signals.
        """
        signaler = self._backend.signaler

        # for conductor
        signaler.eip_process_restart_tls.connect(self._do_eip_restart)
        signaler.eip_process_restart_ping.connect(self._do_eip_restart)
        signaler.eip_process_finished.connect(self._eip_finished)

        # for widget
        self._eip_status.connect_backend_signals()

    def start_eip_machine(self, action):
        """
        Initializes and starts the EIP state machine.
        Needs the reference to the eip_status widget not to be empty.

        :action: QtAction
        """
        action = action
        button = self._eip_status.eip_button
        label = self._eip_status.eip_label

        builder = statemachines.ConnectionMachineBuilder(self.eip_connection)
        eip_machine = builder.make_machine(button=button,
                                           action=action,
                                           label=label)
        self.eip_machine = eip_machine
        self.eip_machine.start()
        logger.debug('eip machine started')

    def do_connect(self):
        """
        Start the connection procedure.
        Emits a signal that triggers the OFF -> Connecting sequence.
        This will call _start_eip via the state machine.
        """
        self.qtsigs.do_connect_signal.emit()

    @QtCore.Slot()
    def _start_eip(self):
        """
        Starts EIP.
        """
        # FIXME --- pass is_restart parameter to here ???
        is_restart = self._eip_status and self._eip_status.is_restart

        def reconnect():
            self.qtsigs.disconnecting_signal.connect(self._stop_eip)

        if is_restart:
            QtDelayedCall(0, reconnect)
        else:
            self._eip_status.eip_pre_up()
        self.user_stopped_eip = False

        # Until we set an option in the preferences window, we'll assume that
        # by default we try to autostart. If we switch it off manually, it
        # won't try the next time.
        self._settings.set_autostart_eip(True)
        self._eip_status.is_restart = False

        # DO the backend call!
        self._backend.eip_start()

    @QtCore.Slot()
    def _stop_eip(self, restart=False):
        """
        TRIGGERS:
          self.qsigs.do_disconnect_signal (via state machine)

        Stops vpn process and makes gui adjustments to reflect
        the change of state.

        :param restart: whether this is part of a eip restart.
        :type restart: bool
        """
        self._eip_status.is_restart = restart
        self.user_stopped_eip = not restart

        def reconnect_stop_signal():
            self.qtsigs.disconnecting_signal.connect(self._stop_eip)

        def on_disconnected_do_restart():
            # hard restarts
            eip_status_label = self._eip_status.tr("{0} is restarting")
            eip_status_label = eip_status_label.format(self.eip_name)
            self._eip_status.eip_stopped(restart=True)
            self._eip_status.set_eip_status(eip_status_label, error=False)

            QtDelayedCall(1000, self.qtsigs.do_connect_signal.emit)

        if restart:
            # we bypass the on_eip_disconnected here
            #qtsigs.disconnected_signal.disconnect()
            self.qtsigs.disconnected_signal.connect(on_disconnected_do_restart)
            QtDelayedCall(0, self.qtsigs.disconnected_signal.emit)

        # Call to the backend.
        self._backend.eip_stop(restart=restart)

        self._eip_status.set_eipstatus_off(False)
        self._already_started_eip = False

        logger.debug('Setting autostart to: False')
        self._settings.set_autostart_eip(False)

        if self._logged_user:
            self._eip_status.set_provider(
                make_address(
                    self._logged_user,
                    self._get_best_provider_config().get_domain()))

        self._eip_status.eip_stopped(restart=restart)

        if restart:
            QtDelayedCall(50000, reconnect_stop_signal)

    @QtCore.Slot()
    def _do_eip_restart(self):
        """
        TRIGGERS:
            self._eip_connection.qtsigs.process_restart

        Restart the connection.
        """
        if self._eip_status is not None:
            self._eip_status.is_restart = True
        try:
            self.qtsigs.disconnecting_signal.disconnect()
        except Exception:
            logger.error("cannot disconnect signals")

        def do_stop(*args):
            self._stop_eip(restart=True)

        self.qtsigs.disconnecting_signal.connect(do_stop)
        self.qtsigs.do_disconnect_signal.emit()

    @QtCore.Slot(int)
    def _eip_finished(self, exitCode):
        """
        TRIGGERS:
            Signaler.eip_process_finished

        Triggered when the EIP/VPN process finishes to set the UI
        accordingly.

        Ideally we would have the right exit code here,
        but the use of different wrappers (pkexec, cocoasudo) swallows
        the openvpn exit code so we get zero exit in some cases  where we
        shouldn't. As a workaround we just use a flag to indicate
        a purposeful switch off, and mark everything else as unexpected.

        :param exitCode: the exit code of the eip process.
        :type exitCode: int
        """
        # TODO Add error catching to the openvpn log observer
        # so we can have a more precise idea of which type
        # of error did we have (server side, local problem, etc)

        logger.info("VPN process finished with exitCode %s..."
                    % (exitCode,))

        signal = self.qtsigs.disconnected_signal

        # XXX check if these exitCodes are pkexec/cocoasudo specific
        if exitCode in (126, 127):
            eip_status_label = self._eip_status.tr(
                "{0} could not be launched "
                "because you did not authenticate properly.")
            eip_status_label = eip_status_label.format(self.eip_name)
            self._eip_status.set_eip_status(eip_status_label, error=True)
            signal = self.qtsigs.connection_aborted_signal
            self._backend.eip_terminate()

        # XXX FIXME --- check exitcode is != 0 really
        if exitCode != 0 and not self.user_stopped_eip:
            eip_status_label = self._eip_status.tr(
                "{0} finished in an unexpected manner!")
            eip_status_label = eip_status_label.format(self.eip_name)
            self._eip_status.eip_stopped()
            self._eip_status.set_eip_status_icon("error")
            self._eip_status.set_eip_status(eip_status_label,
                                            error=True)
            signal = self.qtsigs.connection_died_signal

        if exitCode == 0 and IS_MAC:
            # XXX remove this warning after I fix cocoasudo.
            logger.warning("The above exit code MIGHT BE WRONG.")

        # We emit signals to trigger transitions in the state machine:
        signal.emit()
