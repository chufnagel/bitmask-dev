DIST=dist/bitmask
NEXT_VERSION = $(shell cat pkg/next-version)
DIST_VERSION = $(DIST)-$(NEXT_VERSION)/

HEADLESS_DIST=dist/bitmask-nox
HEADLESS_DIST_VERSION = $(HEADLESS_DIST)-$(NEXT_VERSION)/

ANONVPN_DIST=dist/anonvpn
ANONVPN_DIST_VERSION = $(ANONVPN_DIST)-$(NEXT_VERSION)/

BITMASK_ROOT = src/leap/bitmask/vpn/helpers/linux/bitmask-root
POLKIT_POLICY = src/leap/bitmask/vpn/helpers/linux/se.leap.bitmask.bundle.policy
SUDO = /usr/bin/sudo

include pkg/bundles/build.mk
include pkg/thirdparty/openvpn/build.mk

dev-bootstrap:
	pkg/tools/bitmask-bootstrap.sh

dev-mail:
	pip install -U -e '.[mail]'

dev-gui: install_pixelated
	pip install -U -e '.[gui]'

dev-backend:
	pip install -U -e '.[backend]'

dev-all: install_pixelated
	pip install -I --install-option="--bundled" pysqlcipher
	pip install -U -e '.[all]'

dev-latest-leap:
	pip install -U -e 'git+https://0xacab.org/leap/leap_pycommon@master#egg=leap.common'
	pip install -U -e 'git+https://0xacab.org/leap/soledad@master#egg=leap.soledad'

dev-latest-backend: dev-backend dev-latest-leap

dev-latest-all: dev-all dev-latest-leap

upgrade-all:
	python pkg/tools/upgrade_all.py

uninstall:
	pip uninstall leap.bitmask

test:
	tox

test_e2e_mail: install_helpers
	tests/e2e/e2e-test-mail.sh

test_e2e_vpn: install_helpers
	tests/e2e/e2e-test-vpn.sh

test_e2e_conditional_downloads: install_helpers
	tests/e2e/conditional_downloads.py

test_functional_setup:
	pip install -U behave selenium

test_functional: install_helpers
	test -f /usr/bin/lxpolkit && lxpolkit &
	xvfb-run --server-args="-screen 0 1280x1024x24" behave --tags ~@wip --tags @smoke tests/functional/features -k --no-capture -D host=localhost

test_functional_graphical:
	behave --tags ~@wip --tags @smoke tests/functional/features -k --no-capture -D host=localhost

test_functional_graphical_wip:
	behave --tags @wip tests/functional/features -k --no-capture -D host=localhost

install_helpers:
	# if there's no sudo, assumming this is running as root by the CI
	test -f $(SUDO) && sudo cp $(BITMASK_ROOT) /usr/local/sbin/ || cp $(BITMASK_ROOT) /usr/local/sbin/
	test -f $(SUDO) && sudo cp $(POLKIT_POLICY) /usr/share/polkit-1/actions/se.leap.bitmask.bundle.policy || cp $(POLKIT_POLICY) /usr/share/polkit-1/actions/se.leap.bitmask.bundle.policy

install_pixelated:
	pip install leap.pixelated leap.pixelated-www

qt-resources:
	pyrcc5 pkg/branding/icons.qrc -o src/leap/bitmask/gui/app_rc.py

doc:
	cd docs && make html

bundle_in_virtualenv:
	pkg/build_bundle_with_venv.sh

bundle_in_virtualenv_osx:
	pkg/build_osx_bundle_with_venv.sh

package_in_docker:
	# needs docker_ce and gitlab-runner from upstream
	gitlab-runner exec docker package:amd64_stretch

bundler_image:
	cd pkg/docker_bundler && make bundler

bundle_in_docker:
	# needs a docker container called 'bitmask-bundler-apt', created with 'make bundler_image'
	# XXX why was it needed to specify -u `id -u` again? if it's something with gilab CI we might need
	# to chown it first.
	cat pkg/bundle_from_docker | docker run -i -v /srv/bitmask-builds:/dist -w /dist -e REPO="$(REPO)" -e BRANCH="$(BRANCH)" bitmask-bundler-apt bash

bundle_riseupvpn:
	# needs a docker container called 'bitmask-bundler-apt', created with 'make bundler_image'
	# XXX why was it needed to specify -u `id -u` again? if it's something with gilab CI we might need
	# to chown it first.
	cat pkg/bundle_riseupvpn_from_docker | docker run -i -v /srv/bitmask-builds:/dist -w /dist -e REPO="$(REPO)" -e BRANCH="$(BRANCH)" bitmask-bundler-apt bash

bundle_riseupvpn_osx_in_virtualenv:
	pkg/build_osx_bundle_riseupvpn_with_venv.sh

bundle_in_docker_virtualenv:
	# this runs bundles inside a virtualenv. it is kind of slow because it compiles all python extensions in dependencies each time.
	cat pkg/bundle_from_docker_venv | docker run -i -v ~/leap/bitmask-dev:/dist -w /dist -u `id -u` -e REPO="$(REPO)" -e BRANCH="$(BRANCH)" bitmask-bundler-venv bash
	mkdir -p dist/
	cp -r bitmaskbuild/$(DIST_VERSION) dist/
	rm -rf bitmaskbuild

upload:
	python setup.py sdist bdist_wheel --universal upload --sign -i kali@leap.se -r pypi

clean:
	find . -type f -name "*.py[co]" -delete
	find . -type d -name "__pycache__" -delete
