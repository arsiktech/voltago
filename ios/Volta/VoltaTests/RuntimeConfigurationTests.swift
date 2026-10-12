import XCTest
@testable import Volta

final class ReleaseEndpointPolicyTests: XCTestCase {
    func testAcceptsAStableASCIIPublicHTTPSServiceRoot() {
        for (rawValue, expected) in [
            ("https://api.volta-service.es", "https://api.volta-service.es"),
            ("  https://API.volta-service.es/  ", "https://api.volta-service.es"),
            ("https://api.volta-service.es:8443", "https://api.volta-service.es:8443"),
        ] {
            let url = ReleaseEndpointPolicy.resolvedURL(from: rawValue)
            XCTAssertEqual(url.absoluteString, expected, rawValue)
            XCTAssertTrue(ReleaseEndpointPolicy.isConfigured(url), rawValue)
        }
    }

    func testMissingOrCleartextServiceEndpointFailsClosed() {
        for rawValue in [nil, "", "   ", "http://api.volta-service.es"] {
            XCTAssertEqual(
                ReleaseEndpointPolicy.resolvedURL(from: rawValue),
                ReleaseEndpointPolicy.unconfiguredURL,
                rawValue ?? "nil"
            )
        }
        XCTAssertFalse(ReleaseEndpointPolicy.isConfigured(ReleaseEndpointPolicy.unconfiguredURL))
    }

    func testReservedAndLocalHostnamesFailClosed() {
        for rawValue in [
            "https://localhost",
            "https://api.localhost",
            "https://charger.local",
            "https://charger.internal",
            "https://charger.invalid",
            "https://charger.test",
            "https://charger.example",
            "https://charger.home.arpa",
        ] {
            assertServiceFailsClosed(rawValue)
        }
    }

    func testUnicodePunycodeAndPercentEncodedHostnamesFailClosed() {
        for rawValue in [
            "https://cargador.español.es",
            "https://xn--cargador-9za.es",
            "https://api.xn--volta-9za.es",
            "https://%31%32%37.0.0.1",
            "https://%65xample.com",
        ] {
            assertServiceFailsClosed(rawValue)
        }
    }

    func testEveryIPLiteralAndLegacyNumericFormFailsClosed() {
        for rawValue in [
            "https://127.0.0.1",
            "https://192.168.1.220",
            "https://8.8.8.8",
            "https://[::1]",
            "https://[2001:4860:4860::8888]",
            "https://2130706433",
            "https://0177.0.0.1",
            "https://0x7f.0.0.1",
            "https://0x7f000001",
        ] {
            assertServiceFailsClosed(rawValue)
        }
    }

    func testServiceEndpointRejectsCredentialsQueryFragmentAndNonRootPaths() {
        for rawValue in [
            "https://user:password@api.volta-service.es",
            "https://api.volta-service.es?token=secret",
            "https://api.volta-service.es#fragment",
            "https://api.volta-service.es/api",
            "https://api.volta-service.es/%2Fapi",
            "https://api.volta-service.es//",
            "https://api.volta-service.es:",
            "https://api.volta-service.es:0",
            "https://api.volta-service.es:65536",
        ] {
            assertServiceFailsClosed(rawValue)
        }
    }

    func testHostedDocumentPolicyAllowsPathsButKeepsTheSameTransportAndHostBoundary() {
        XCTAssertEqual(
            ReleaseEndpointPolicy.resolvedDocumentURL(
                from: "https://legal.volta-service.es/privacy"
            )?.absoluteString,
            "https://legal.volta-service.es/privacy"
        )
        XCTAssertEqual(
            ReleaseEndpointPolicy.resolvedDocumentURL(
                from: "https://legal.volta-service.es:8443/support/contact"
            )?.absoluteString,
            "https://legal.volta-service.es:8443/support/contact"
        )

        for rawValue in [
            nil,
            "https://configuration.invalid/privacy",
            "http://legal.volta-service.es/privacy",
            "https://legal.example/privacy",
            "https://xn--volta-9za.es/privacy",
            "https://127.0.0.1/privacy",
            "https://legal.volta-service.es:/privacy",
            "https://user@legal.volta-service.es/privacy",
            "https://legal.volta-service.es/privacy?locale=en",
            "https://legal.volta-service.es/privacy#collection",
            "https://legal.volta-service.es/%2e%2e/terms",
        ] {
            XCTAssertNil(
                ReleaseEndpointPolicy.resolvedDocumentURL(from: rawValue),
                rawValue ?? "nil"
            )
        }
    }

    private func assertServiceFailsClosed(
        _ rawValue: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertEqual(
            ReleaseEndpointPolicy.resolvedURL(from: rawValue),
            ReleaseEndpointPolicy.unconfiguredURL,
            rawValue,
            file: file,
            line: line
        )
    }
}

#if DEBUG
final class DebugEndpointMigrationTests: XCTestCase {
    func testRetiredSimulatorLANEndpointMigratesToCurrentLoopbackDefault() {
        XCTAssertEqual(
            VoltaRuntimeConfiguration.migratedDebugBaseURLString(
                "http://192.168.1.220:8773"
            ),
            "http://127.0.0.1:8773"
        )
    }

    func testExplicitCustomDebugEndpointsRemainUntouched() {
        for endpoint in [
            "http://127.0.0.1:49152",
            "http://192.168.1.99:9000",
            "https://dev.voltago.app",
        ] {
            XCTAssertEqual(
                VoltaRuntimeConfiguration.migratedDebugBaseURLString(endpoint),
                endpoint
            )
        }
    }
}
#endif
