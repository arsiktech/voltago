import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
}

// The one place that names the website. The manifest and the Digital Asset Links statement
// read these values, so a host change happens here and in frontend/public/.well-known/.
val twaHost = "voltago.app"
// The query marks Android launches (it reaches in-app feedback through location.search);
// the web app and its service worker ignore unknown parameters.
val twaStartUrl = "https://$twaHost/?source=android-twa"

// Upload-key signing. The keystore and its passwords stay outside the repository.
// Point VOLTA_ANDROID_KEYSTORE_PROPERTIES, or `volta.keystoreProperties` in the git-ignored
// android/local.properties, at a file with storeFile, storePassword, keyAlias and keyPassword.
fun readProperties(file: File): Properties = Properties().apply { file.inputStream().use(::load) }

val keystorePropertiesPath: String? =
    providers.environmentVariable("VOLTA_ANDROID_KEYSTORE_PROPERTIES").orNull
        ?: rootProject.file("local.properties").takeIf { it.isFile }
            ?.let { readProperties(it).getProperty("volta.keystoreProperties") }

val uploadKey: Properties? = keystorePropertiesPath?.let { path ->
    val file = file(path)
    require(file.isFile) { "Keystore properties file not found: $path" }
    readProperties(file)
}

android {
    namespace = "tech.arsik.volta"
    compileSdk = 36

    defaultConfig {
        applicationId = "tech.arsik.volta"
        // Android 8.0: current Chrome no longer supports older releases, so a TWA cannot run there.
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "1.0.0"

        manifestPlaceholders["twaHost"] = twaHost
        manifestPlaceholders["twaStartUrl"] = twaStartUrl
        resValue(
            "string",
            "asset_statements",
            """[{"relation":["delegate_permission/common.handle_all_urls"],"target":{"namespace":"web","site":"https://$twaHost"}}]""",
        )
    }

    buildFeatures {
        resValues = true
    }

    signingConfigs {
        if (uploadKey != null) {
            create("upload") {
                storeFile = file(uploadKey.getProperty("storeFile"))
                storePassword = uploadKey.getProperty("storePassword")
                keyAlias = uploadKey.getProperty("keyAlias")
                keyPassword = uploadKey.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
            signingConfig = signingConfigs.findByName("upload")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation(libs.androidbrowserhelper)
    implementation(libs.locationdelegation)
}

// A release without the upload key would be an unsigned bundle that Play rejects; stop early
// with the fix instead.
val requireUploadKey = tasks.register("requireUploadKey") {
    val configured = uploadKey != null
    doLast {
        if (!configured) {
            throw GradleException(
                "Release builds need the upload key. Set VOLTA_ANDROID_KEYSTORE_PROPERTIES to the " +
                    "keystore properties file (see android/README.md).",
            )
        }
    }
}
tasks.matching { it.name == "preReleaseBuild" }.configureEach { dependsOn(requireUploadKey) }
