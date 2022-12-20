FILESEXTRAPATHS_prepend := "${THISDIR}/files:"

DESCRIPTION = "AOS VIS"

GO_IMPORT = "github.com/aoscloud/aos_vis"

LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://src/${GO_IMPORT}/LICENSE;md5=3b83ef96387f14655fc854ddc3c6bd57"

BRANCH = "main"
SRCREV = "9a9187a23290e45584756125ec7f7023fc2b7cf5"
SRC_URI = "git://${GO_IMPORT}.git;branch=${BRANCH};protocol=https"

SRC_URI += " \
    file://aos_vis.cfg \
    file://aos-vis.service \
    file://aos-target.conf \
"

inherit go goarch systemd

SYSTEMD_SERVICE_${PN} = "aos-vis.service"

VIS_DATA_PROVIDER ?= "renesassimulatoradapter"

AOS_VIS_PLUGINS ?= " \
    plugins/vinadapter \
    plugins/boardmodeladapter \
    plugins/subjectsadapter \
"

python __anonymous() {
    if d.getVar('VIS_DATA_PROVIDER'):
        d.appendVar('AOS_VIS_PLUGINS', 'plugins/${VIS_DATA_PROVIDER}')
}

VIS_CERTS_PATH = "${base_prefix}/usr/share/aos/vis/certs"

FILES_${PN} += " \
    ${sysconfdir} \
    ${systemd_system_unitdir} \
    ${VIS_CERTS_PATH} \
"

RDEPENDS_${PN} += " \
    aos-rootca \
    ${@bb.utils.contains('VIS_DATA_PROVIDER', 'telemetryemulatoradapter', 'telemetry-emulator', '', d)} \
"

RDEPENDS_${PN}-dev += " bash make"
RDEPENDS_${PN}-staticdev += " bash make"

# embed version
GO_LDFLAGS += '-ldflags="-X main.GitSummary=`git --git-dir=${S}/src/${GO_IMPORT}/.git describe --tags --always`"'

# WA to support go install for v 1.18

GO_LINKSHARED = ""

do_compile_prepend() {
    cd ${GOPATH}/src/${GO_IMPORT}/
}

do_prepare_adapters() {
    if [ -z "${AOS_VIS_PLUGINS}" ]; then
        exit 0
    fi

    file="${S}/src/${GO_IMPORT}/plugins/plugins.go"

    echo 'package plugins' > ${file}
    echo 'import (' >> ${file}

    for plugin in ${AOS_VIS_PLUGINS}; do
        echo "\t_ \"${GO_IMPORT}/${plugin}\"" >> ${file}
    done

    echo ')' >> ${file}
}

python do_configure_adapters() {
    import json

    file_name = oe.path.join(d.getVar("D"), d.getVar("sysconfdir"), "aos", "aos_vis.cfg")

    with open(file_name) as f:
        data = json.load(f)

    adapter_list = [os.path.basename(adapter) for adapter in d.getVar("AOS_VIS_PLUGINS").split()]
    data["Adapters"] = [adapter for adapter in data["Adapters"] if adapter["Plugin"] in adapter_list]

    with open(file_name, "w") as f:
        json.dump(data, f, indent=4)
}

do_install_append() {
    install -d ${D}${sysconfdir}/aos
    install -m 0644 ${WORKDIR}/aos_vis.cfg ${D}${sysconfdir}/aos

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/aos-vis.service ${D}${systemd_system_unitdir}/aos-vis.service

    install -d ${D}${sysconfdir}/systemd/system/aos.target.d
    install -m 0644 ${WORKDIR}/aos-target.conf ${D}${sysconfdir}/systemd/system/aos.target.d/${PN}.conf

    if "${@bb.utils.contains('VIS_DATA_PROVIDER', 'telemetryemulatoradapter', 'true', 'false', d)}"; then
        sed -i -e 's/network-online.target/network-online.target telemetry-emulator.service/g' ${D}${systemd_system_unitdir}/aos-vis.service
        sed -i -e '/ExecStart/i ExecStartPre=/bin/sleep 1' ${D}${systemd_system_unitdir}/aos-vis.service
    fi

    install -d ${D}${VIS_CERTS_PATH}
    install -m 0644 ${S}/src/${GO_IMPORT}/data/*.pem ${D}${VIS_CERTS_PATH}
}

pkg_postinst_${PN}() {
    # Add wwwivi to /etc/hosts
    if ! grep -q 'wwwivi' $D${sysconfdir}/hosts ; then
        echo '127.0.0.1	wwwivi' >> $D${sysconfdir}/hosts
    fi
}

addtask prepare_adapters after do_unpack before do_compile
addtask configure_adapters after do_install before do_package
