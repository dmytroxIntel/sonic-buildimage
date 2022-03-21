try:
    import os
    import sys
    import errno
    import datetime
    import logging
    import logging.config
    import yaml
    import re

    sys.path.append(os.path.dirname(__file__))

    if sys.version_info.major == 3:
        from io import StringIO
    else:
        from cStringIO import StringIO

    from sonic_platform_base.sonic_eeprom import eeprom_base
    from sonic_platform_base.sonic_eeprom import eeprom_tlvinfo

    from sonic_py_common import device_info

    from sonic_platform.platform_thrift_client import thrift_try
    from sonic_platform.platform_utils import file_create

except ImportError as e:
    raise ImportError (str(e) + "- required module not found")

def tlv_tlv_eeprom_get(client):
    return client.pltfm_mgr.pltfm_mgr_tlv_eeprom_get()

TLV_TLV_EEPROM_API_SUPPORT = False
if device_info.get_platform() in ["x86_64-accton_as9516_32d-r0", "x86_64-accton_as9516bf_32d-r0"]:
    try:
        from thrift.Thrift import TApplicationException
        thrift_try(tlv_tlv_eeprom_get, 1)
        TLV_TLV_EEPROM_API_SUPPORT = True
    except TApplicationException as e:
        pass

_platform_eeprom_map = {
    "prod_name"         : ("Product Name",                "0x21", 12),
    "odm_pcba_part_num" : ("Part Number",                 "0x22", 13),
    "prod_ser_num"      : ("Serial Number",               "0x23", 12),
    "ext_mac_addr"      : ("Extended MAC Address Base",   "0x24", 12),
    "sys_mfg_date"      : ("System Manufacturing Date",   "0x25",  4),
    "prod_ver"          : ("Product Version",             "0x26",  1),
    "ext_mac_addr_size" : ("Extende MAC Address Size",    "0x2A",  2),
    "sys_mfger"         : ("Manufacturer",                "0x2B",  8)
}

_product_dict = {
    "Montara"   : "Wedge100BF-32X-O-AC-F-BF",
    "Lower MAV" : "Wedge100BF-65X-O-AC-F-BF",
    "Upper MAV" : "Wedge100BF-65X-O-AC-F-BF"
}

_EEPROM_SYMLINK = "/var/run/platform/eeprom/syseeprom"
_EEPROM_STATUS = "/var/run/platform/eeprom/status"

def get_tlv_eeprom():
    try:
        return bytearray.fromhex(thrift_try(tlv_tlv_eeprom_get).raw_content_hex)
    except Exception:
        raise RuntimeError("eeprom.py: Initialization failed")

def get_platform_eeprom():
    def sys_eeprom_get(client):
        return client.pltfm_mgr.pltfm_mgr_sys_eeprom_get()
    try:
        return thrift_try(sys_eeprom_get)
    except Exception:
        raise RuntimeError("eeprom.py: Initialization failed")

def platfrom_eeprom_to_params(platform_eeprom):
    eeprom_params = ""
    for attr, val in platform_eeprom.__dict__.items():
        if val is None:
            continue

        elem = _platform_eeprom_map.get(attr)
        if elem is None:
            continue

        if isinstance(val, str):
            value = val.replace('\0', '')
        else:
            value = str(val)

        if attr == "sys_mfg_date":
            value = datetime.datetime.strptime(value, '%m-%d-%y').strftime('%m/%d/%Y 00:00:00')

        product = _product_dict.get(value)
        if product is not None:
            value = product
        if len(eeprom_params) > 0:
            eeprom_params += ","
        eeprom_params += "{0:s}={1:s}".format(elem[1], value)
    return eeprom_params

class Eeprom(eeprom_tlvinfo.TlvInfoDecoder):
    def __init__(self):
        file_create(_EEPROM_SYMLINK, '646')
        file_create(_EEPROM_STATUS, '646')
        super(Eeprom, self).__init__(_EEPROM_SYMLINK, 0, _EEPROM_STATUS, True)

        self.report_status("initializing..")
        self._eeprom_bin = bytearray()
        if TLV_TLV_EEPROM_API_SUPPORT:
            self._eeprom_bin = get_tlv_eeprom()
        else:
            eeprom_params = platfrom_eeprom_to_params(get_platform_eeprom())
            stdout_stream = sys.stdout
            sys.stdout = open(os.devnull, 'w')
            self._eeprom_bin = self.set_eeprom(self._eeprom_bin, [eeprom_params])
            sys.stdout.close()
            sys.stdout = stdout_stream
        try:
            self.write_eeprom(self._eeprom_bin)
            self.report_status("ok")
        except IOError as e:
            logging.error("Failed to write eeprom : %s" % (str(e)))

        visitor = SavingTlvVisitor()
        self.visit_eeprom(self._eeprom_bin, visitor)
        self._system_eeprom_info = visitor.get_contents()

    def get_contents(self):
        return self._eeprom_bin, self._system_eeprom_info

    def report_status(self, status):
        status_file = None
        try:
            status_file = open(_EEPROM_STATUS, "w")
            status_file.write(status)
        except IOError as e:
            logging.error("Failed to report state : %s" % (str(e)))
        finally:
            if status_file is not None:
                status_file.close()

class SavingTlvVisitor(eeprom_tlvinfo.EepromDefaultVisitor):
    def __init__(self):
        self._tlv_dict = dict()

    def visit_tlv(self, name, code, length, value):
        if code != Eeprom._TLV_CODE_VENDOR_EXT:
            self._tlv_dict["0x{:X}".format(code)] = value.rstrip('\0')
        else:
            if value:
                value = value.rstrip('\0')
                if value:
                    code = "0x{:X}".format(code)
                    if code not in self._tlv_dict:
                        self._tlv_dict[code] = [value]
                    else:
                        self._tlv_dict[code].append(value)

    def set_error(self, error):
        logging.error("SavingTlvVisitor error : %s" % (str(error)))

    def get_contents(self):
        return self._tlv_dict
