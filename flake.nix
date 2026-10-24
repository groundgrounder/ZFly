{
  description = "ZFly standalone GTK proxy client with an app-owned mihomo core";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in {
      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          python = pkgs.python3.withPackages (ps: [ ps.pygobject3 ]);
          desktopItem = pkgs.makeDesktopItem {
            name = "io.github.zfly.ZFly";
            desktopName = "ZFly";
            comment = "Standalone proxy and subscription manager";
            exec = "zfly";
            icon = "io.github.zfly.ZFly";
            categories = [ "Network" ];
            terminal = false;
          };
        in {
          default = pkgs.stdenvNoCC.mkDerivation {
            pname = "zfly";
            version = builtins.head (builtins.match ".*__version__ = \"([^\"]+)\".*" (builtins.readFile ./runtime/zfly_runtime/__init__.py));
            src = ./.;
            nativeBuildInputs = [ pkgs.wrapGAppsHook4 pkgs.gobject-introspection pkgs.makeWrapper ];
            buildInputs = [ pkgs.gtk4 pkgs.libadwaita pkgs.adwaita-icon-theme ];
            dontBuild = true;
            doCheck = true;
            checkPhase = ''
              export PYTHONDONTWRITEBYTECODE=1
              PYTHONPATH=linux:runtime ${python}/bin/python3 -m unittest discover -s linux/tests -v
              PYTHONPATH=runtime ${python}/bin/python3 -m unittest discover -s runtime/tests -v
            '';
            installPhase = ''
              runHook preInstall
              mkdir -p "$out/share/zfly" "$out/bin"
              cp -r linux/zfly "$out/share/zfly/"
              cp -r runtime/zfly_runtime "$out/share/zfly/zfly_runtime"
              cp -r ${desktopItem}/share/applications "$out/share/"
              install -Dm644 ${./assets/icon.svg} "$out/share/icons/hicolor/scalable/apps/io.github.zfly.ZFly.svg"
              for size in 16 32 48 64 128 256 512 1024; do
                install -Dm644 ${./assets/icons}/zfly-$size.png \
                  "$out/share/icons/hicolor/''${size}x''${size}/apps/io.github.zfly.ZFly.png"
              done
              install -Dm644 ${./assets/tray-outline.svg} \
                "$out/share/icons/hicolor/scalable/status/zfly-tray-stopped-symbolic.svg"
              install -Dm644 ${./assets/tray-filled.svg} \
                "$out/share/icons/hicolor/scalable/status/zfly-tray-running-symbolic.svg"
              for state in stopped running; do
                install -Dm644 ${./assets/icons}/zfly-tray-''${state}-symbolic.png \
                  "$out/share/icons/hicolor/32x32/status/zfly-tray-''${state}-symbolic.png"
              done
              # Desktop panels can cache a failed theme-name lookup before a
              # profile is updated. Point the launcher at our immutable image.
              substituteInPlace "$out/share/applications/io.github.zfly.ZFly.desktop" \
                --replace-fail "Icon=io.github.zfly.ZFly" \
                  "Icon=$out/share/icons/hicolor/256x256/apps/io.github.zfly.ZFly.png"
              makeWrapper ${python}/bin/python3 "$out/bin/zfly" \
                --add-flags "-m zfly" \
                --set ZFLY_EXECUTABLE "$out/bin/zfly" \
                --set PYTHONDONTWRITEBYTECODE 1 \
                --set ZFLY_CORE ${pkgs.mihomo}/bin/mihomo \
                --set ZFLY_CORE_KIND mihomo \
                --prefix XDG_DATA_DIRS : ${pkgs.adwaita-icon-theme}/share \
                --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.glib ]} \
                --prefix PYTHONPATH : "$out/share/zfly"
              runHook postInstall
            '';
            meta = {
              description = "Standalone GTK proxy client with profiles, subscriptions and mihomo";
              mainProgram = "zfly";
              license = pkgs.lib.licenses.gpl3Only;
              platforms = systems;
            };
          };
        });
      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
          python = pkgs.python3.withPackages (ps: [ ps.pygobject3 ]);
        in {
          default = pkgs.mkShell {
            packages = [ python pkgs.gtk4 pkgs.libadwaita pkgs.adwaita-icon-theme pkgs.glib pkgs.dbus pkgs.xvfb-run ];
            nativeBuildInputs = [ pkgs.wrapGAppsHook4 pkgs.gobject-introspection ];
            shellHook = ''
              export XDG_DATA_DIRS=${pkgs.adwaita-icon-theme}/share:''${XDG_DATA_DIRS:-}
              export PYTHONDONTWRITEBYTECODE=1
            '';
          };
        });
      checks = forAllSystems (system: { package = self.packages.${system}.default; });
      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/zfly";
        };
      });
    };
}
