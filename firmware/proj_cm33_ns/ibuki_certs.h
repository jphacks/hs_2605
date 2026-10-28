/* Public CA certificates the board trusts (public, not secrets; taken from macOS's trust store
 * on 2026-10-28 and checked by SHA-256 fingerprint).
 *
 * eduroam at Hiroshima University: its login server eduroam-radius.hiroshima-u.ac.jp has a
 * certificate from "NII Open Domain CA - G8 RSA" (SECOM, for Japan's universities), under
 * "SECOM TLS RSA Root CA 2024". The board trusts exactly these two. Infineon's
 * enterprise-security library checks the chain but not the server's name, so a server with a
 * certificate from the same university CA would also be accepted; a random fake "eduroam" is not.
 *   SECOM TLS RSA Root CA 2024   SHA-256 14:35:F2:25:...:EB:37:2F:93, until 2049
 *   NII Open Domain CA - G8 RSA  SHA-256 7A:4A:D9:E1:...:83:A5:3F:FC, until 2040
 *
 * ntfy.sh (phone alerts) uses Let's Encrypt:
 *   ISRG Root X1                 SHA-256 96:BC:EC:06:...:BD:DF:08:C6, until 2035 */
#ifndef IBUKI_CERTS_H
#define IBUKI_CERTS_H

#define IBUKI_EDUROAM_CA_PEM \
    "-----BEGIN CERTIFICATE-----\n" \
    "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\n" \
    "-----END CERTIFICATE-----\n" \
    "-----BEGIN CERTIFICATE-----\n" \
    "MIIGxTCCBK2gAwIBAgIQR6WRJpp5L3/kVvgzQ6Wr1zANBgkqhkiG9w0BAQwFADBa\n" \
    "MQswCQYDVQQGEwJKUDEmMCQGA1UEChMdU0VDT00gVHJ1c3QgU3lzdGVtcyBDby4s\n" \
    "IEx0ZC4xIzAhBgNVBAMTGlNFQ09NIFRMUyBSU0EgUm9vdCBDQSAyMDI0MB4XDTI1\n" \
    "MDgyMTA1MDY1OFoXDTQwMDgyMTAwMDAwMFowWzELMAkGA1UEBhMCSlAxJjAkBgNV\n" \
    "BAoTHVNFQ09NIFRydXN0IFN5c3RlbXMgQ28uLCBMdGQuMSQwIgYDVQQDExtOSUkg\n" \
    "T3BlbiBEb21haW4gQ0EgLSBHOCBSU0EwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAw\n" \
    "ggIKAoICAQDHxjgregvmw61GKwFoRoGEKMkqpiiE5Ni4amkG7bBafiEj46HWi7Gx\n" \
    "o4O6YOk4Ay51jgwJRC73hR47jCmXXTtGoyHhkUjDKdlnPHsBGJMxMcJDlDMaWG8o\n" \
    "/Olxu2bZBNMyuzQoo8MYsHmgTnoTo4cEhpcUxslvncveWrZoGBJG8xm0mVHOKYZr\n" \
    "VUiGbyoarmzs61KtNMfZewyEmnhKstPEE4YB2UsM0g4DCCfN+xzWST8zlhWYPb2Q\n" \
    "VRpcmKT/pfYmNHMKaCfEHK6CFY552UpBy6RgqR7cJeSglFS3WSgZNc9wc5v6fBjy\n" \
    "CkYGYLFBCmQAJGkzf5DVaf8QdKZ3a+tF3TFNIgzDuZWz1G7VoSFD5cWh81UDA7Ue\n" \
    "eN/ytxx7TmIUbPQr+lpcXYq2bmEylGggc3H81gUhdmqDjRO0zjGEMilhvXDyhH3V\n" \
    "lYyn3qo41wOW3gubPYRwFHfBK9b3p12lRmxhe3Nm68MaCl2mAf3H4SzSUKutx0Aq\n" \
    "rtW0PtyHnYZiRGMubnCtJFzUF/nCxXFAjEl/Fkcp4zdcUL1InbJYENou/eaXdUas\n" \
    "TKQHCchyPJedAIkKG7cCxBGAf1pdY+xCW4kHbWnsdpGnG1vS7L0/9MI8wsxw/4FK\n" \
    "gMP0Unbi3r7c1H0HwjGO0yuTXw55FiE5CLdZscTGJxd+rwZpJb/DUQIDAQABo4IB\n" \
    "hDCCAYAwHwYDVR0jBBgwFoAULOtyEo5Yd2Q1FVY1AVcHqX0NNuYwHQYDVR0OBBYE\n" \
    "FJKqOLEYWitcS+ehCkCl8kQp2MBTMA4GA1UdDwEB/wQEAwIBBjAhBgNVHSAEGjAY\n" \
    "MAgGBmeBDAECAjAMBgoqgwiMmxtkhwULMBIGA1UdEwEB/wQIMAYBAf8CAQAwEwYD\n" \
    "VR0lBAwwCgYIKwYBBQUHAwEwTQYDVR0fBEYwRDBCoECgPoY8aHR0cDovL3JlcG8x\n" \
    "LnNlY29tdHJ1c3QubmV0L3Jvb3QvdGxzcnNhL3Rsc3JzYXJvb3RjYTIwMjQuY3Js\n" \
    "MIGSBggrBgEFBQcBAQSBhTCBgjA2BggrBgEFBQcwAYYqaHR0cDovL3Rsc3JzYXJv\n" \
    "b3RjYTIwMjQub2NzcC5zZWNvbS1jZXJ0LmpwMEgGCCsGAQUFBzAChjxodHRwOi8v\n" \
    "cmVwbzIuc2Vjb210cnVzdC5uZXQvcm9vdC90bHNyc2EvdGxzcnNhcm9vdGNhMjAy\n" \
    "NC5jZXIwDQYJKoZIhvcNAQEMBQADggIBAKk1NNHifDnN/IXxPg9d3MPXtTE6/5Tp\n" \
    "maP4fwsy8Vdb2T5UArgHtcxc7RFtJqL+lfHM9aoSSqDBsRGrsARlh850gQenVG4u\n" \
    "Js62j1JYOhdr2q4t7xLWZ8EBLEo/xLW2iPvJ/voaUtsu/9pUGevPAUEvYuv+tyjw\n" \
    "+CPEnwG1MdPLhYWpDPXn83PLpkE9jHd/4TP6k+t8qN6S8H/Y7g4TNVvbvZZPJQNd\n" \
    "fDEbqHgJqEXIRE51ssgWbzHteCAu5Cr3qK2/BFoQqWBB0UMkonBvmYxlw5P/JENj\n" \
    "ux8KmPfI0pypptwszJSE9OBqgq4gFkR/cfnFZ+Bmhfk3SgQc2dFiJKc3IHnerXB4\n" \
    "37Lvh4SUZgwzL3ypKmPUAHyEqJlcpSr+POAwvaXiQzpusvSWynix09e7jofpO9Go\n" \
    "qEvwfM6G2zuU9r3brz5RqBj5rT5/+p3dceDCvao4jtnbca3kBHNkLshfi7hMNKti\n" \
    "XPOzVgFDG30y0M+F7VxlxsDVr0rm4aXHrtGi7jI0s3hqXuiZ+M68mP6PiIo9viUA\n" \
    "rs3I9mR/ExnkrJ47twEdi5aRduOBjyTs4OaItMGWv5O5RUneGh725x0oJhg6kdyD\n" \
    "8ONNfmRLLa3xGbOSWrTJFsAF1cg9LlW2TgVYCMVWhIB0DpC41du8KEcx80/iNOu4\n" \
    "ZrO3MBAKFqTY\n" \
    "-----END CERTIFICATE-----\n"

#define IBUKI_NTFY_ROOT_CA_PEM \
    "-----BEGIN CERTIFICATE-----\n" \
    "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\n" \
    "-----END CERTIFICATE-----\n"

#endif /* IBUKI_CERTS_H */
