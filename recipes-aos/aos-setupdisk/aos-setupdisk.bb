DESCRIPTION = "Aos provisioning finish script"

LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/Apache-2.0;md5=89aea4e17d99a7cacdbeed46a0096b10"

SRC_URI = " \
    file://setupdisk.sh \
    file://aosdisk.cfg \
"

S = "${WORKDIR}"

FILES_${PN} = " \
    ${aos_opt_dir} \
"

RDEPENDS_${PN} = " \
    diskencryption \
    lvm2 \
    lvm2-udevrules \
    e2fsprogs \
    quota \
"

do_install() {
    install -d ${D}${aos_opt_dir}
    install -m 0755 ${S}/setupdisk.sh ${D}${aos_opt_dir}
    install -m 0644 ${S}/aosdisk.cfg ${D}${aos_opt_dir}
}
