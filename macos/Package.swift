// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ZFly",
    defaultLocalization: "zh-Hans",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "ZFly", targets: ["ZFly"])],
    targets: [
        .target(name: "ZFlyKit", resources: [.copy("Resources/preview.json")]),
        .executableTarget(name: "ZFly", dependencies: ["ZFlyKit"], resources: [.process("Resources")]),
        .testTarget(name: "ZFlyTests", dependencies: ["ZFly", "ZFlyKit"]),
    ]
)
