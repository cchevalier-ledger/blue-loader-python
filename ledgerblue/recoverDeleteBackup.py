from ledgerblue.comm import getDongle
from ledgerblue.hexLoader import HexLoader
from ledgerblue.recoverMutualAuth import recoverMutualAuth, SCPv3, recoverValidate
from ledgerblue.recoverUtil import Recover
from Crypto.Cipher import AES
import argparse
import os
import json
import gnupg


def get_argparser():
    parser = argparse.ArgumentParser(description="Delete shares backups")
    parser.add_argument("--targetId", help="The device's target ID (default is Nano X)", type=auto_int)
    parser.add_argument("--rootPrivateKey", help="The private key of the Certificate Authority")
    parser.add_argument("--issuerPublicKey", help="The public key of the Issuer (used to verify the certificate of "
                                                  "the device)")
    parser.add_argument("-s", "--select", help="Select the backups to use", required = True, action="store",
                        dest="backups", type=str, nargs='*')
    parser.add_argument("-c", "--configuration", help="Configuration file", default=None, required=True, action="store")
    parser.add_argument("--gpg", help="Decrypt the deletion public key. Enter the email address "
                                      "associated to your gpg key", default=None, action="store")
    return parser


def auto_int(x):
    return int(x, 0)


if __name__ == '__main__':

    args = get_argparser().parse_args()

    # Read the configuration file
    try:
        with open(args.configuration,'r') as file:
            conf = json.load(file)
    except Exception as err:
        print(err)
        exit()

    if args.targetId is None:
        args.targetId = 0x33000004
    if args.rootPrivateKey is None:
        raise Exception("Missing Certificate Authority private key")
    if args.issuerPublicKey is None:
        args.issuerPublicKey = '0490f5c9d15a0134bb019d2afd0bf297149738459706e7ac5be4abc350a1f81805' \
                               '7224fce12ec9a65de18ec34d6e8c24db927835ea1692b14c32e9836a75dad609'
    if not args.gpg:
        print("Reading your deletion public key from unencrypted files")
    else:
        home = os.path.join(os.environ['HOME'], ".gnupg")
        gpg = gnupg.GPG(gnupghome=home)

    dongle = getDongle(True)

    orchestratorPrivateKey = conf['orchestrator']['key']

    # Execute device-orchestrator secure channel protocol
    secret, devicePublicKey = SCPv3(dongle, bytearray.fromhex(args.issuerPublicKey), args.targetId,
                                    bytearray.fromhex(args.rootPrivateKey),
                                    bytearray.fromhex(orchestratorPrivateKey))
    loader = HexLoader(dongle, 0xe0, True, secret, scpv3=True)

    # Initialize the session with the info from the configuration file
    recoverSession = Recover(conf)
    confirmed = False

    for backup in args.backups:
        # Read the deletion public key
        try:
            with open(backup, 'r') as file:
                if args.gpg:
                    enc_delete_data = file.read()
                    dec_data = gpg.decrypt(enc_delete_data)
                    delete_data = json.loads(dec_data.data)
                else:
                    delete_data = json.load(file)
        except Exception as err:
            print(err)
            exit()

        for p in conf['providers']:
            if p['name'] == list(delete_data.keys())[0]:
                break
        name = p['name']
        privateKey = p['key']
        backupPublicKey = delete_data[name]['public_key']

        # Execute device-provider mutual authentication
        providerSk, providerPk = recoverValidate(loader, bytearray.fromhex(args.rootPrivateKey), name,
                                                 bytearray.fromhex(privateKey))
        loader.recoverMutualAuth()
        sharedKey = recoverMutualAuth(providerSk, devicePublicKey)
        recoverSession.sharedKey = sharedKey

        # Send the user identity to the device
        if not confirmed:
            dataIdv = recoverSession.recoverPrepareDataIdv()
            cipher = AES.new(sharedKey, AES.MODE_SIV)
            ciphertext, tag = cipher.encrypt_and_digest(dataIdv)
            loader.recoverConfirmID(tag, ciphertext)
            confirmed = True

        dataHash = recoverSession.recoverPrepareDataHash(providerPk)
        cipher = AES.new(sharedKey, AES.MODE_SIV)
        ciphertext, tag = cipher.encrypt_and_digest(dataHash)

        # Validate backup data hash (device and backup provider agree on the same backup data)
        response = loader.recoverValidateHash(tag, ciphertext)

        # Verify whether the backup can be deleted
        recoverSession.recoverDeleteBackup(loader, bytearray.fromhex(backupPublicKey))
