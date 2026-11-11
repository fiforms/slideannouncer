#!/bin/bash
# Guided workflow for a multi-developer RAUC signing PKI.
#
#   One person (the "CA holder") owns an offline root CA. Every developer
#   (including the CA holder) has their own signing key + a certificate
#   issued by that CA. Devices trust only the CA, so adding/removing a
#   developer never requires touching a device.
#
#   ./rauc-pki.sh ca                  CA holder, once: create the root CA
#   ./rauc-pki.sh csr <name>          Developer: make a key + signing request
#   ./rauc-pki.sh sign <file.csr>     CA holder: issue a certificate for a CSR
#   ./rauc-pki.sh install <chain.pem> Developer: install the returned cert
#   ./rauc-pki.sh env                 Print the .env lines to use
#   ./rauc-pki.sh status              Show what exists + the next step
#   ./rauc-pki.sh verify [bundle]     Check your cert chain (and a bundle)
#
# Everything lives in $PKI_HOME (default ~/.slideannouncer-pki), deliberately
# outside the git repo. Private keys never leave the machine that made them:
# only .csr files travel to the CA holder, and only certificate chains
# (public) travel back.
set -euo pipefail

PKI_HOME="${PKI_HOME:-$HOME/.slideannouncer-pki}"
CA_DIR="$PKI_HOME/ca"            # CA holder only
KEY_DIR="$PKI_HOME/keys"         # your signing keys + CSRs
SIGNED_DIR="$PKI_HOME/signed"    # CA holder: issued chains, ready to send back
TRUSTED_CA="$PKI_HOME/trusted-ca-cert.pem"   # the CA cert devices will trust

CA_KEY="$CA_DIR/ca-key.pem"
CA_CERT="$CA_DIR/ca-cert.pem"
ISSUED_LOG="$CA_DIR/issued.log"
ORG="Slide Announcer"

TMPD="$(mktemp -d)"
tmpfile() { mktemp -p "$TMPD"; }
trap 'rm -rf "$TMPD"' EXIT
die() { echo "rauc-pki.sh: $*" >&2; exit 1; }
hr()  { echo "------------------------------------------------------------"; }
need() { command -v "$1" >/dev/null 2>&1 || die "'$1' not found in PATH"; }
need openssl

fingerprint() { openssl x509 -in "$1" -noout -fingerprint -sha256 | cut -d= -f2; }
valid_name() { [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]]; }
# Subject CN of a CSR / cert.
csr_cn()  { openssl req  -in "$1" -noout -subject -nameopt multiline | sed -n 's/^ *commonName *= *//p'; }
cert_cn() { openssl x509 -in "$1" -noout -subject -nameopt multiline | sed -n 's/^ *commonName *= *//p'; }
# Last PEM block in a file (the CA cert, in a leaf-first chain).
last_cert() {
	awk '/-----BEGIN CERTIFICATE-----/{buf=""} {buf=buf $0 "\n"} /-----END CERTIFICATE-----/{last=buf} END{printf "%s", last}' "$1"
}
first_cert() {
	awk '/-----BEGIN CERTIFICATE-----/{p=1} p{print} /-----END CERTIFICATE-----/{exit}' "$1"
}

usage() { sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit "${1:-1}"; }

# ---------------------------------------------------------------------------
cmd_ca() {
	[ ! -e "$CA_KEY" ] || die "a CA already exists at $CA_DIR — refusing to overwrite it"
	echo "== Step: create the root CA (CA holder, once) =="
	echo
	echo "You'll be asked for a passphrase that encrypts the CA private key."
	echo "It is the root of trust for every device you ship. Choose a strong"
	echo "one and store it SEPARATELY from the key file."
	echo
	read -r -p "CA common name [$ORG Fleet Root CA]: " CN
	CN="${CN:-$ORG Fleet Root CA}"
	read -r -p "CA validity in days [7300 (~20 years)]: " DAYS
	DAYS="${DAYS:-7300}"

	umask 077
	mkdir -p "$CA_DIR" "$SIGNED_DIR"
	openssl genrsa -aes256 -out "$CA_KEY" 4096
	# pathlen:0 — the CA can issue signing certs but those can't issue more.
	openssl req -x509 -new -key "$CA_KEY" -sha256 -days "$DAYS" \
		-subj "/O=$ORG/CN=$CN" \
		-addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
		-addext "keyUsage=critical,keyCertSign,cRLSign" \
		-addext "subjectKeyIdentifier=hash" \
		-out "$CA_CERT"
	chmod 644 "$CA_CERT"
	: > "$ISSUED_LOG"
	cp "$CA_CERT" "$TRUSTED_CA"
	local FP; FP="$(fingerprint "$CA_CERT")"

	echo
	hr
	echo "CA created."
	echo "  CA certificate (public): $CA_CERT"
	echo "  CA private key:          $CA_KEY"
	echo "  CA SHA-256 fingerprint:  $FP"
	echo
	echo "NEXT:"
	echo "  1. BACK UP $CA_KEY to encrypted offline media NOW, and store its"
	echo "     passphrase somewhere different. Losing both = you can never"
	echo "     issue or rotate signing certs without reflashing every device."
	echo "  2. Tell every developer this fingerprint over a channel other than"
	echo "     where you send certs (chat + voice, etc.), so they can confirm"
	echo "     they're trusting the real CA when they run 'install':"
	echo "       $FP"
	echo "  3. Make your own signing key:  ./rauc-pki.sh csr <your-name>"
	echo "     Everyone else does the same, then sends you the .csr it prints."
	hr
}

# ---------------------------------------------------------------------------
cmd_csr() {
	local NAME="${1:-}"
	[ -n "$NAME" ] || die "usage: $0 csr <your-name>   (letters, digits, . _ - only; e.g. alice)"
	valid_name "$NAME" || die "name may only contain letters, digits, '.', '_' and '-'"
	local KEY="$KEY_DIR/$NAME-key.pem" CSR="$KEY_DIR/$NAME.csr"
	[ ! -e "$KEY" ] || die "$KEY already exists — pick another name or remove it deliberately"

	echo "== Step: create your signing key + certificate request =="
	umask 077
	mkdir -p "$KEY_DIR"
	openssl genrsa -out "$KEY" 4096 2>/dev/null
	chmod 600 "$KEY"
	openssl req -new -key "$KEY" -sha256 -subj "/O=$ORG/CN=$NAME" -out "$CSR"
	chmod 644 "$CSR"

	echo
	hr
	echo "Created:"
	echo "  Private key (KEEP SECRET, never send): $KEY"
	echo "  Signing request (safe to send):        $CSR"
	echo
	echo "NEXT:"
	echo "  Send this CSR to the CA holder for signing:"
	echo "      $CSR"
	echo "  (it contains only your public key; email/chat is fine)."
	echo "  Ask them to run:   ./rauc-pki.sh sign $NAME.csr"
	echo "  When they send back '$NAME-chain.pem', run:"
	echo "      ./rauc-pki.sh install /path/to/$NAME-chain.pem"
	hr
}

# ---------------------------------------------------------------------------
cmd_sign() {
	local CSR="${1:-}" DAYS="${2:-365}"
	[ -n "$CSR" ] && [ -f "$CSR" ] || die "usage: $0 sign <file.csr> [validity-days, default 365]"
	[ -f "$CA_KEY" ] && [ -f "$CA_CERT" ] || die "no CA at $CA_DIR — run '$0 ca' first (on the CA holder's machine)"

	openssl req -in "$CSR" -noout -verify 2>/dev/null \
		|| die "CSR signature check failed — the file is corrupt or not a CSR"
	local CN; CN="$(csr_cn "$CSR")"
	[ -n "$CN" ] || die "CSR has no common name"
	valid_name "$CN" || die "CSR common name '$CN' has unexpected characters; ask the requester to redo it with a simple name"

	echo "== Step: issue a signing certificate (CA holder) =="
	echo
	echo "Request:   $CSR"
	echo "Subject:   $(openssl req -in "$CSR" -noout -subject)"
	echo "Key:       $(openssl req -in "$CSR" -noout -text | sed -n 's/.*Public-Key: *//p' | head -1)"
	echo "Validity:  $DAYS days"
	echo
	echo "Anyone holding the matching private key will be able to sign OTA"
	echo "bundles that every device trusts. Confirm that '$CN' is who you"
	echo "think it is, and that this CSR really came from them."
	read -r -p "Issue certificate for '$CN'? [y/N] " OK
	[ "$OK" = "y" ] || [ "$OK" = "Y" ] || die "aborted"

	umask 022
	mkdir -p "$SIGNED_DIR"
	local EXT; EXT="$(tmpfile)"
	cat > "$EXT" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=codeSigning
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid
EOF
	local SERIAL; SERIAL="0x$(openssl rand -hex 16 | sed 's/^[89a-f]/1/')"
	local CERT="$SIGNED_DIR/$CN-cert.pem" CHAIN="$SIGNED_DIR/$CN-chain.pem"
	[ ! -e "$CHAIN" ] || mv "$CHAIN" "$CHAIN.$(date +%s).old"
	echo "Enter the CA key passphrase when prompted:"
	openssl x509 -req -in "$CSR" -CA "$CA_CERT" -CAkey "$CA_KEY" \
		-set_serial "$SERIAL" -days "$DAYS" -sha256 -extfile "$EXT" -out "$CERT" 2>/dev/null \
		|| die "signing failed (wrong CA passphrase?)"
	# Leaf first, then the CA — what RAUC's --cert wants.
	cat "$CERT" "$CA_CERT" > "$CHAIN"
	openssl verify -CAfile "$CA_CERT" -purpose codesign "$CERT" >/dev/null \
		|| die "issued cert failed verification (should not happen)"
	echo "$(date -u +%FT%TZ) serial=$SERIAL cn=$CN days=$DAYS expires=$(openssl x509 -in "$CERT" -noout -enddate | cut -d= -f2)" >> "$ISSUED_LOG"

	echo
	hr
	echo "Issued certificate for '$CN' (logged in $ISSUED_LOG)."
	echo "  Certificate chain (public): $CHAIN"
	echo "  CA SHA-256 fingerprint:     $(fingerprint "$CA_CERT")"
	echo
	echo "NEXT:"
	echo "  Send this file back to $CN:"
	echo "      $CHAIN"
	echo "  Remind $CN to compare the CA fingerprint above with the one you"
	echo "  gave them earlier, then run:"
	echo "      ./rauc-pki.sh install /path/to/$CN-chain.pem"
	hr
}

# ---------------------------------------------------------------------------
cmd_install() {
	local CHAIN="${1:-}"
	[ -n "$CHAIN" ] && [ -f "$CHAIN" ] || die "usage: $0 install <name-chain.pem>"
	local CN LEAF CA; LEAF="$(tmpfile)"; CA="$(tmpfile)"
	first_cert "$CHAIN" > "$LEAF"; last_cert "$CHAIN" > "$CA"
	[ -s "$LEAF" ] && [ -s "$CA" ] || die "no certificates found in $CHAIN"
	CN="$(cert_cn "$LEAF")"
	valid_name "$CN" || die "unexpected certificate name '$CN'"
	local KEY="$KEY_DIR/$CN-key.pem"
	[ -f "$KEY" ] || die "no private key $KEY — was this cert issued for a CSR made on this machine ('$0 csr $CN')?"

	# The cert must match the key you made, and chain to the included CA.
	[ "$(openssl x509 -in "$LEAF" -noout -pubkey | sha256sum)" = "$(openssl pkey -in "$KEY" -pubout | sha256sum)" ] \
		|| die "this certificate does not match your private key $KEY"
	openssl verify -CAfile "$CA" -purpose codesign "$LEAF" >/dev/null \
		|| die "certificate does not verify against the CA in the chain (wrong purpose / expired / bad chain)"

	local FP; FP="$(fingerprint "$CA")"
	echo "== Step: install your signed certificate =="
	echo
	echo "Certificate for: $CN  (expires $(openssl x509 -in "$LEAF" -noout -enddate | cut -d= -f2))"
	echo "CA fingerprint (SHA-256) in this chain:"
	echo "    $FP"
	if [ -f "$TRUSTED_CA" ] && [ "$(fingerprint "$TRUSTED_CA")" = "$FP" ]; then
		echo "Matches the CA you already trust."
	else
		echo
		echo "This becomes the CA baked into every image you build. It MUST equal the"
		echo "fingerprint the CA holder gave you out-of-band. If it doesn't, stop."
		read -r -p "Does the fingerprint match? [y/N] " OK
		[ "$OK" = "y" ] || [ "$OK" = "Y" ] || die "aborted"
		umask 022
		cp "$CA" "$TRUSTED_CA"
	fi
	umask 022
	cp "$CHAIN" "$KEY_DIR/$CN-chain.pem"

	echo
	hr
	echo "Installed: $KEY_DIR/$CN-chain.pem"
	echo
	echo "NEXT:"
	echo "  Put these in image-builder/.env (or run '$0 env' to print them):"
	"$0" env "$CN" | sed 's/^/      /'
	echo "  Devices built before this must be reflashed once, because their keyring"
	echo "  still holds the old dev cert instead of this CA."
	hr
}

# ---------------------------------------------------------------------------
# Pick the signing identity: given name, else the only chain present.
pick_identity() {
	local NAME="${1:-}"
	if [ -z "$NAME" ]; then
		local found=("$KEY_DIR"/*-chain.pem)
		[ -e "${found[0]}" ] || die "no installed certificate in $KEY_DIR (see '$0 status')"
		[ "${#found[@]}" -eq 1 ] || die "several identities installed; pass a name: $(basename -a "${found[@]}" | sed 's/-chain.pem//' | tr '\n' ' ')"
		NAME="$(basename "${found[0]}" -chain.pem)"
	fi
	echo "$NAME"
}

cmd_env() {
	local NAME; NAME="$(pick_identity "${1:-}")"
	[ -f "$TRUSTED_CA" ] || die "no trusted CA cert at $TRUSTED_CA"
	echo "RAUC_CERT_PATH=$KEY_DIR/$NAME-chain.pem"
	echo "RAUC_KEY_PATH=$KEY_DIR/$NAME-key.pem"
	echo "RAUC_KEYRING_CERT_PATH=$TRUSTED_CA"
}

# ---------------------------------------------------------------------------
cmd_verify() {
	local NAME; NAME="$(pick_identity "")"
	local CHAIN="$KEY_DIR/$NAME-chain.pem" LEAF; LEAF="$(tmpfile)"
	first_cert "$CHAIN" > "$LEAF"
	openssl verify -CAfile "$TRUSTED_CA" -purpose codesign "$LEAF"
	echo "expires: $(openssl x509 -in "$LEAF" -noout -enddate | cut -d= -f2)"
	if [ -n "${1:-}" ]; then
		need rauc
		# Same keyring settings as the device's system.conf.
		local CONF; CONF="$(tmpfile)"
		printf '[system]\ncompatible=x\nbootloader=noop\n[keyring]\npath=%s\ncheck-purpose=codesign\n' "$TRUSTED_CA" > "$CONF"
		rauc --conf="$CONF" info "$1"
	fi
}

# ---------------------------------------------------------------------------
cmd_status() {
	echo "PKI home: $PKI_HOME"
	echo
	if [ -f "$CA_KEY" ]; then
		echo "[CA holder] CA exists: $CA_CERT"
		echo "            fingerprint: $(fingerprint "$CA_CERT")"
		echo "            issued so far: $(wc -l < "$ISSUED_LOG" 2>/dev/null || echo 0) cert(s) (see $ISSUED_LOG)"
	else
		echo "[CA holder] no CA on this machine (only the CA holder runs '$0 ca')"
	fi
	echo
	local any=0
	for k in "$KEY_DIR"/*-key.pem; do
		[ -e "$k" ] || continue
		any=1
		local n; n="$(basename "$k" -key.pem)"
		if [ -f "$KEY_DIR/$n-chain.pem" ]; then
			local l; l="$(tmpfile)"; first_cert "$KEY_DIR/$n-chain.pem" > "$l"
			echo "[identity] $n: certificate installed, expires $(openssl x509 -in "$l" -noout -enddate | cut -d= -f2)"
		else
			echo "[identity] $n: key + CSR made, waiting for a signed cert."
			echo "           NEXT: send $KEY_DIR/$n.csr to the CA holder, then '$0 install $n-chain.pem'"
		fi
	done
	[ "$any" = 1 ] || echo "[identity] none yet — run '$0 csr <your-name>'"
}

case "${1:-}" in
	ca)      shift; cmd_ca "$@" ;;
	csr)     shift; cmd_csr "$@" ;;
	sign)    shift; cmd_sign "$@" ;;
	install) shift; cmd_install "$@" ;;
	env)     shift; cmd_env "$@" ;;
	verify)  shift; cmd_verify "$@" ;;
	status)  shift; cmd_status "$@" ;;
	-h|--help|help) usage 0 ;;
	*) usage ;;
esac
