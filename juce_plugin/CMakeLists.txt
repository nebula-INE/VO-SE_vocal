# ==============================================================================
# juce_plugin/CMakeLists.txt
#
# VO-SE vocal JUCE Plugin ビルド設定（完全自動・最適化版）
# ==============================================================================

cmake_minimum_required(VERSION 3.22)

# ★ 二重 project() を解消（CXX のみで十分。C は JUCE 側が必要なら要求する）
project(VoseVocalPlugin VERSION 0.1.0 LANGUAGES C CXX)

# ========================
# C++ 標準設定
# ========================
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# ========================
# FetchContent のベースディレクトリをワークスペース外に固定
# ------------------------------------------------------------
# 背景:
#   GitHub Actions の actions/cache は CMakeCache.txt に焼き込まれた
#   絶対パスごとキャッシュする。ワークスペース名
#   (例: VO-SE6 → VO-SE_vocal) が変わると FetchContent subbuild が
#   「CMakeCache.txt の directory が違う」で必ず失敗する。
#
# 対処:
#   FETCHCONTENT_BASE_DIR を $HOME/.cache 配下に固定し、
#   GitHub Actions のキャッシュ path もそこを指すようにする。
#   これでワークスペースパスがキャッシュに混入しなくなる。
# ========================
if(WIN32)
    set(_VOSE_CACHE_HOME "$ENV{USERPROFILE}")
else()
    set(_VOSE_CACHE_HOME "$ENV{HOME}")
endif()
if(NOT _VOSE_CACHE_HOME)
    set(_VOSE_CACHE_HOME "${CMAKE_BINARY_DIR}/.fetchcontent-cache")
endif()

set(FETCHCONTENT_BASE_DIR
    "${_VOSE_CACHE_HOME}/.cache/juce-fetchcontent"
    CACHE PATH "Stable FetchContent base dir (shared across workspaces)" FORCE)

message(STATUS "[VO-SE] FETCHCONTENT_BASE_DIR = ${FETCHCONTENT_BASE_DIR}")

# ========================
# JUCE フレームワークの取得
# ========================
include(FetchContent)
FetchContent_Declare(
    JUCE
    GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
    GIT_TAG 8.0.5
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(JUCE)

# ========================
# 外部ライブラリ検出 (macOS 用 iconv)
# ========================
if(APPLE)
    find_package(Iconv REQUIRED)   # Iconv::Iconv ターゲットが生成される
endif()

# ========================
# プラグインフォーマット定義
# ========================
set(VOSE_PLUGIN_FORMATS VST3 Standalone)
if(APPLE)
    list(APPEND VOSE_PLUGIN_FORMATS AU)
endif()

# ========================
# JUCE プラグイン定義
# ========================
juce_add_plugin(VoseVocal
    COMPANY_NAME              "VO-SE Project"
    PLUGIN_BUNDLE_ID          "com.vose-project.vosevocal"
    PLUGIN_MANUFACTURER_CODE  Vose
    PLUGIN_CODE               Vse1
    FORMATS                   ${VOSE_PLUGIN_FORMATS}
    PRODUCT_NAME              "VO-SE vocal"
    NEEDS_MIDI_INPUT          TRUE
    IS_SYNTH                  TRUE
    COPY_PLUGIN_AFTER_BUILD   FALSE
)

# ========================
# ソースファイル定義
# ========================
file(GLOB VOSE_SOURCES
    "${CMAKE_CURRENT_SOURCE_DIR}/../src/*.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/../include/*.h"
)

# ★ プラグインでは不要なファイルを除外 ★
list(REMOVE_ITEM VOSE_SOURCES
    "${CMAKE_CURRENT_SOURCE_DIR}/../src/vose_streaming_final.cpp"
)

if(NOT VOSE_SOURCES)
    message(FATAL_ERROR "[VO-SE] No source files found in src/ !")
endif()

target_sources(VoseVocal PRIVATE ${VOSE_SOURCES})

# ========================
# インクルードディレクトリ
# ========================
target_include_directories(VoseVocal
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/include
        ${CMAKE_CURRENT_SOURCE_DIR}/../src
        ${CMAKE_CURRENT_SOURCE_DIR}/../include
)

# ========================
# コンパイル定義（全プラットフォーム共通）
# ========================
target_compile_definitions(VoseVocal
    PUBLIC
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_VST3_CAN_REPLACE_VST2=0
)

# ========================
# Linux 固有設定
# ========================
if(UNIX AND NOT APPLE)
    target_compile_definitions(VoseVocal PUBLIC JUCE_ALSA=1)
endif()

# ========================
# macOS 固有設定
# ========================
if(APPLE)
    if(TARGET VoseVocal)
        set_target_properties(VoseVocal PROPERTIES
            MACOSX_DEPLOYMENT_TARGET "11.0"
        )
    endif()
    if(TARGET VoseVocal_SharedCode)
        set_target_properties(VoseVocal_SharedCode PROPERTIES
            MACOSX_DEPLOYMENT_TARGET "11.0"
        )
    endif()
endif()

# ========================
# Windows 固有設定
# ========================
if(WIN32)
    target_compile_options(VoseVocal PRIVATE /wd4996)
endif()

# ========================
# JUCE ライブラリのリンク（共通）
# ========================
target_link_libraries(VoseVocal
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_lto_flags
        juce::juce_recommended_warning_flags
    PRIVATE
        juce::juce_audio_utils
        juce::juce_audio_processors
        juce::juce_gui_basics
        juce::juce_dsp
)

# ========================
# macOS 用 iconv リンク
# ========================
if(APPLE)
    target_link_libraries(VoseVocal PRIVATE Iconv::Iconv)

    foreach(subtarget IN ITEMS VoseVocal_SharedCode VoseVocal_VST3 VoseVocal_AU VoseVocal_Standalone)
        if(TARGET ${subtarget})
            target_link_libraries(${subtarget} PRIVATE Iconv::Iconv)
        endif()
    endforeach()
endif()

# ========================
# ビルド構成のデバッグ出力
# ========================
message(STATUS "=======================================================")
message(STATUS "[VO-SE] Plugin Build Configuration:")
message(STATUS "  - CMAKE_BUILD_TYPE:      ${CMAKE_BUILD_TYPE}")
message(STATUS "  - PLUGIN_FORMATS:        ${VOSE_PLUGIN_FORMATS}")
message(STATUS "  - SOURCE_DIR:            ${CMAKE_CURRENT_SOURCE_DIR}")
message(STATUS "  - FETCHCONTENT_BASE_DIR: ${FETCHCONTENT_BASE_DIR}")
message(STATUS "  - JUCE_TAG:              8.0.5")
message(STATUS "=======================================================")
