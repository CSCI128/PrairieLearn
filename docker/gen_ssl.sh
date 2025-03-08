#! /bin/bash

KEY=/etc/pki/tls/private/backus.mines.edu.key
CERT=/etc/pki/tls/certs/backus.mines.edu.crt
CA_CHAIN=/etc/pki/tls/certs/server-chain.crt

if [ -e $KEY ]; then
    exit 0
fi

openssl req -x509 -out $CERT -keyout $KEY \
    -newkey rsa:2048 -nodes -sha256 \
    -subj '/CN=pl' -extensions EXT -config <( \
    printf "[dn]\nCN=pl\n[req]\ndistinguished_name = dn\n[EXT]\nsubjectAltName=DNS:pl\nkeyUsage=digitalSignature\nextendedKeyUsage=serverAuth")

# Make sure the file exists but can be empty
touch $CA_CHAIN
