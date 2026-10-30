fn main() {
    println!("cargo:rerun-if-changed=src/robot_bridge.c");
    println!("cargo:rerun-if-env-changed=MUJOCO_DIR");
    if std::env::var_os("CARGO_FEATURE_ROBOTICS").is_none() {
        return;
    }
    let dir=std::path::PathBuf::from(std::env::var_os("MUJOCO_DIR").expect("Set MUJOCO_DIR to the official MuJoCo 3.3.7 SDK (Python wheel directory includes headers and library)."));
    cc::Build::new()
        .file("src/robot_bridge.c")
        .include(dir.join("include"))
        .compile("scenward_robot_bridge");
    let out = std::path::PathBuf::from(std::env::var_os("OUT_DIR").unwrap());
    let target = std::env::var("CARGO_CFG_TARGET_OS").unwrap();
    let source = std::fs::read_dir(&dir)
        .unwrap()
        .filter_map(Result::ok)
        .map(|e| e.path())
        .find(|p| {
            p.file_name().unwrap().to_string_lossy()
                == if target == "macos" {
                    "libmujoco.3.3.7.dylib"
                } else {
                    "libmujoco.so.3.3.7"
                }
        })
        .expect("MuJoCo 3.3.7 shared library missing");
    let link = out.join(if target == "macos" {
        "libmujoco.dylib"
    } else {
        "libmujoco.so"
    });
    std::fs::copy(&source, &link).unwrap();
    if target == "macos" {
        let framework = out.join("mujoco.framework/Versions/A");
        std::fs::create_dir_all(&framework).unwrap();
        std::fs::copy(&source, framework.join("libmujoco.3.3.7.dylib")).unwrap();
        println!("cargo:rustc-link-arg=-Wl,-rpath,{}", out.display());
        println!("cargo:rustc-link-arg=-Wl,-rpath,@executable_path/../Frameworks");
    }
    println!("cargo:rustc-link-search=native={}", out.display());
    println!("cargo:rustc-link-lib=mujoco");
    println!("cargo:rustc-link-arg=-Wl,-rpath,{}", dir.display());
}
