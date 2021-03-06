# This makefile should be called from the topmost bitmask folder

OSX_RES = dist/Bitmask.app/Contents/Resources
OSX_CON = dist/Bitmask.app/Contents/MacOS
OSX_ANON_RES= dist/RiseupVPN.app/Contents/Resources
OSX_ANON_CON= dist/RiseupVPN.app/Contents/MacOS
OSX_CERT = "Developer ID Installer: LEAP Encryption Access Project"
BUILD_RELEASE?=no

default:
	echo "enter 'make bundle or make bundle_osx'"

bundle: bundle_clean
	pyinstaller -y pkg/pyinst/app.spec
	cp $(VIRTUAL_ENV)/lib/python2.7/site-packages/_scrypt.so $(DIST)
	cp src/leap/bitmask/core/bitmaskd.tac $(DIST)
	mkdir -p $(DIST)/leap
	# if you find yourself puzzled becase the following files are not found in your
	# virtualenv, make sure that you're installing the packages from wheels and not eggs.
	mkdir -p $(DIST)/leap/soledad/client/_db
	cp $(VIRTUAL_ENV)/lib/python2.7/site-packages/leap/soledad/client/_db/dbschema.sql $(DIST)/leap/soledad/client/_db/
	cp $(VIRTUAL_ENV)/lib/python2.7/site-packages/leap/common/cacert.pem $(DIST)/
	cp -r $(VIRTUAL_ENV)/lib/python2.7/site-packages/leap/bitmask_js  $(DIST)/leap/
	cp -r $(VIRTUAL_ENV)/lib/python2.7/site-packages/leap/pixelated_www  $(DIST)/leap/
	echo `git describe` > $(DIST)/version
	mv $(DIST) _bundlelib && mkdir $(DIST_VERSION) && mv _bundlelib $(DIST_VERSION)/lib/
	cd pkg/launcher && make
	cp release-notes.rst $(DIST_VERSION)
	cp pkg/launcher/bitmask $(DIST_VERSION)
	# provider pinning
	mkdir -p $(DIST_VERSION)/apps/providers
	cp -r src/leap/bitmask/bonafide/providers/* $(DIST_VERSION)/apps/providers/

bundle_linux_gpg:
	# TODO build it in a docker container!
	mkdir -p $(DIST_VERSION)/apps/mail
	# this is /usr/bin/gpg1 in debian stretch, gpg in ubuntu
	cp /usr/bin/gpg1 $(DIST_VERSION)/apps/mail/gpg
	# workaround for missing libreadline.so.6 in fresh ubuntu
	patchelf --set-rpath '.' $(DIST_VERSION)/apps/mail/gpg
	cp /lib/x86_64-linux-gnu/libusb-0.1.so.4 $(DIST_VERSION)/lib

bundle_linux_vpn:
	mkdir -p $(DIST_VERSION)/apps/vpn
	# TODO verify signature
	# TODO pick 32 bits if needed
	wget https://downloads.leap.se/thirdparty/linux/openvpn/openvpn-x64 -O $(DIST_VERSION)/apps/vpn/openvpn.leap

bundle_linux_helpers:
	mkdir -p $(DIST_VERSION)/apps/helpers
	cp src/leap/bitmask/vpn/helpers/linux/bitmask-root $(DIST_VERSION)/apps/helpers/
	cp src/leap/bitmask/vpn/helpers/linux/se.leap.bitmask.bundle.policy $(DIST_VERSION)/apps/helpers/

bundle_linux_qt_hacks:
	# now we copy some missing qt stuff... this might be fixed by pyinstaller at some point
	# this is "/mesa/libGL.so" in ubuntu
	ls -la /usr/lib/x86_64-linux-gnu/libGL.so.1.2.0
	cp /usr/lib/x86_64-linux-gnu/libGL.so.1.2.0 $(DIST_VERSION)/lib/libGL.so.1 || echo "Cannot copy libGL"


bundle_osx_helpers:
	mkdir -p $(DIST_VERSION)/apps/helpers
	cp src/leap/bitmask/vpn/helpers/osx/bitmask-helper $(DIST_VERSION)/apps/helpers/
	cp src/leap/bitmask/vpn/helpers/osx/bitmask.pf.conf $(DIST_VERSION)/apps/helpers/
	cp pkg/osx/scripts/se.leap.bitmask-helper.plist $(DIST_VERSION)/apps/helpers/
	cp -r pkg/osx/daemon $(DIST_VERSION)/apps/helpers/
	cp -r pkg/osx/openvpn $(DIST_VERSION)/apps/helpers/

bundle_osx_missing:
	# relink _scrypt, it's linked against brew openssl!
	install_name_tool -change /usr/local/opt/openssl/lib/libcrypto.1.0.0.dylib  "@loader_path/libcrypto.1.0.0.dylib" $(DIST_VERSION)/lib/_scrypt.so
	cp $(DIST_VERSION)/lib/_scrypt.so $(OSX_CON)/
	cp $(DIST_VERSION)/lib/bitmaskd.tac $(OSX_CON)/
	cp $(DIST_VERSION)/lib/cacert.pem $(OSX_CON)/
	cp $(DIST_VERSION)/lib/version $(OSX_CON)/
	cp -r $(DIST_VERSION)/lib/leap $(OSX_CON)/
	mv dist/Bitmask.app/Contents/MacOS/bitmask $(OSX_CON)/bitmask-app
	cp pkg/osx/bitmask-wrapper $(OSX_CON)/bitmask
	mkdir -p $(OSX_RES)/bitmask-helper
	mkdir -p $(OSX_RES)/../apps/providers
	cp -r $(DIST_VERSION)/apps/providers/* $(OSX_RES)/../apps/providers
	cp -r $(DIST_VERSION)/apps/helpers/bitmask-helper $(OSX_RES)/bitmask-helper/
	cp -r $(DIST_VERSION)/apps/helpers/bitmask.pf.conf $(OSX_RES)/bitmask-helper/
	cp -r $(DIST_VERSION)/apps/helpers/daemon/daemon.py $(OSX_RES)/bitmask-helper/
	cp -r $(DIST_VERSION)/apps/helpers/openvpn/* $(OSX_RES)/
	wget https://downloads.leap.se/thirdparty/osx/openvpn/openvpn -O $(OSX_RES)/openvpn.leap
	chmod +x $(OSX_RES)/openvpn.leap
	cp ~/leap_thirdparty_build/gpg $(OSX_RES)/gpg
	chmod +x $(OSX_RES)/gpg
	cp release-notes.rst $(OSX_RES)/../../
	cp pkg/osx/uninstall.sh $(OSX_RES)/../../

bundle_osx_pkg:
	pkg/osx/quickpkg --output dist/Bitmask-$(NEXT_VERSION)_pre.pkg --scripts pkg/osx/scripts/ dist/Bitmask.app/
	@if [ $(BUILD_RELEASE) = no ]; then\
		echo "[!] BUILD_RELEASE=no, skipping signature";\
	else\
		echo "[+] Signing the bundle";\
		productsign --sign $(OSX_CERT) dist/Bitmask-$(NEXT_VERSION)_pre.pkg dist/Bitmask-$(NEXT_VERSION).pkg;\
	fi


bundle_linux: bundle bundle_linux_gpg bundle_linux_vpn bundle_linux_helpers bundle_linux_qt_hacks

bundle_osx: bundle bundle_osx_helpers bundle_osx_missing bundle_osx_pkg

bundle_win:
	pyinstaller -y pkg/pyinst/app.spec
	cp ${VIRTUAL_ENV}/Lib/site-packages/_scrypt.pyd $(DIST)
	cp ${VIRTUAL_ENV}/Lib/site-packages/zmq/libzmq.pyd $(DIST)
	cp src/leap/bitmask/core/bitmaskd.tac $(DIST)

bundle_tar:
	cd dist/ && tar cvzf Bitmask-linux64-$(NEXT_VERSION).tar.gz bitmask-$(NEXT_VERSION)

bundle_sign:
	gpg2 -a --sign --detach-sign dist/Bitmask-linux64-$(NEXT_VERSION).tar.gz 

bundle_upload:
	rsync --rsh='ssh' -avztlpog --progress --partial dist/Bitmask-linux64-$(NEXT_VERSION).* downloads.leap.se:./

bundle_clean:
	rm -rf "dist" "build"

bundle_headless:
	pyinstaller -y pkg/pyinst/headless.spec
	cp $(VIRTUAL_ENV)/lib/python2.7/site-packages/_scrypt.so $(HEADLESS_DIST)
	cp src/leap/bitmask/core/bitmaskd.tac $(HEADLESS_DIST)
	cp $(VIRTUAL_ENV)/lib/python2.7/site-packages/leap/common/cacert.pem $(HEADLESS_DIST)/
	mkdir -p $(HEADLESS_DIST)/leap/soledad/client/_db
	cp $(VIRTUAL_ENV)/lib/python2.7/site-packages/leap/soledad/client/_db/dbschema.sql $(HEADLESS_DIST)/leap/soledad/client/_db/
	echo `git describe` > $(HEADLESS_DIST)/version
	mv $(HEADLESS_DIST) _bundlelib && mkdir -p $(HEADLESS_DIST_VERSION) && mv _bundlelib $(HEADLESS_DIST_VERSION)lib/
	ln $(HEADLESS_DIST_VERSION)lib/bitmask-nox $(HEADLESS_DIST_VERSION)lib/bitmask
	mkdir -p $(HEADLESS_DIST_VERSION)apps/mail
	test -f /usr/bin/gpg1 && cp /usr/bin/gpg1 $(HEADLESS_DIST_VERSION)apps/mail/gpg || cp /usr/bin/gpg $(HEADLESS_DIST_VERSION)apps/mail/gpg
	cd pkg/launcher && make
	cp release-notes.rst $(HEADLESS_DIST_VERSION)
	cp pkg/launcher/bitmask $(HEADLESS_DIST_VERSION)

icons_riseup:
	# if this fails, you need to install (does not work on yosemite):
	# brew install imagemagick --with-librsvg
	pkg/osx/makeicons.sh pkg/branding/riseupvpn.svg
	mv icon.icns pkg/branding/riseupvpn.icns

bundle_anonvpn:
	pyinstaller -y pkg/pyinst/anonvpn.spec
	# rm pkg/branding/riseupvpn.icns
	rm -rf icon.iconset
	cp src/leap/bitmask/core/bitmaskd.tac $(ANONVPN_DIST)
	cp $(VIRTUAL_ENV)/lib/python2.7/site-packages/leap/common/cacert.pem $(ANONVPN_DIST)/
	echo `git describe` > $(ANONVPN_DIST)/version
	mv $(ANONVPN_DIST) _bundlelib && mkdir -p $(ANONVPN_DIST_VERSION) && mv _bundlelib $(ANONVPN_DIST_VERSION)lib/
	mkdir -p $(ANONVPN_DIST_VERSION)/apps/providers
	cp -r src/leap/bitmask/bonafide/providers/* $(ANONVPN_DIST_VERSION)/apps/providers/

bundle_anonvpn_helpers_linux:
	# openvpn
	mkdir -p $(ANONVPN_DIST_VERSION)/apps/vpn
	wget https://downloads.leap.se/thirdparty/linux/openvpn/openvpn-x64 -O $(ANONVPN_DIST_VERSION)/apps/vpn/openvpn.leap
	chmod +x $(ANONVPN_DIST_VERSION)/apps/vpn/openvpn.leap
	# helpers
	mkdir -p $(ANONVPN_DIST_VERSION)/apps/helpers
	cp src/leap/bitmask/vpn/helpers/linux/bitmask-root $(ANONVPN_DIST_VERSION)/apps/helpers/
	cp src/leap/bitmask/vpn/helpers/linux/se.leap.bitmask.bundle.policy $(ANONVPN_DIST_VERSION)/apps/helpers/

bundle_anonvpn_osx_missing:
	mv $(OSX_ANON_CON)/anonvpn $(OSX_ANON_CON)/bitmask-app
	cp pkg/osx/bitmask-wrapper $(OSX_ANON_CON)/anonvpn
	mkdir -p $(OSX_ANON_RES)/bitmask-helper
	cp -r pkg/osx/daemon $(OSX_ANON_RES)/bitmask-helper/
	cp -r pkg/osx/openvpn $(OSX_ANON_RES)/bitmask-helper/
	mkdir -p $(OSX_ANON_RES)/../apps/providers
	cp -r src/leap/bitmask/bonafide/providers/* $(OSX_ANON_RES)/../apps/providers/
	cp -r $(ANONVPN_DIST_VERSION)/apps/providers/* $(OSX_ANON_RES)/../apps/providers
	cp src/leap/bitmask/vpn/helpers/osx/bitmask-helper $(OSX_ANON_RES)/bitmask-helper/
	cp src/leap/bitmask/vpn/helpers/osx/bitmask.pf.conf $(OSX_ANON_RES)/bitmask-helper/
	cp pkg/osx/scripts/se.leap.bitmask-helper.plist $(OSX_ANON_RES)/bitmask-helper/
	wget https://downloads.leap.se/thirdparty/osx/openvpn/openvpn -O $(OSX_ANON_RES)/openvpn.leap
	chmod +x $(OSX_ANON_RES)/openvpn.leap

bundle_anonvpn_osx_pkg:
	cp src/leap/bitmask/core/bitmaskd.tac $(OSX_ANON_CON)/
	cp ../bitmask-systray/bitmask-systray $(OSX_ANON_CON)/bitmask-systray
	install_name_tool -change /usr/local/opt/zeromq/lib/libzmq.5.dylib "@loader_path/libzmq.5.dylib" $(OSX_ANON_CON)/bitmask-systray
	pkg/osx/quickpkg --output dist/RiseupVPN-$(NEXT_VERSION)_pre.pkg --scripts pkg/osx/scripts/ dist/RiseupVPN.app/
	@if [ $(BUILD_RELEASE) = no ]; then\
		echo "[!] BUILD_RELEASE=no, skipping signature";\
	else\
		echo "[+] Signing the bundle";\
		productsign --sign $(OSX_CERT) dist/RiseupVPN-$(NEXT_VERSION)_pre.pkg dist/RiseupVPN-$(NEXT_VERSION).pkg;\
	fi

bundle_anonvpn_linux: bundle_anonvpn bundle_anonvpn_helpers_linux

bundle_anonvpn_osx: bundle_anonvpn bundle_anonvpn_osx_missing bundle_anonvpn_osx_pkg
