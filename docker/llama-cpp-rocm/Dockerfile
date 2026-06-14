# llama.cpp server for AMD R9700 (RDNA4 / gfx1201), serving Qwen3.6-27B GGUF + MTP.
#
# Why a custom image: upstream ghcr.io/ggml-org/llama.cpp:server-rocm ships ROCm 7.2.1, whose
# rocBLAS/hipBLASLt are NOT gfx1201-tuned -> ~18 tok/s. AMD's gfx120X-tuned ROCm (7.13) doubles
# decode to ~34 tok/s. That tuned stack is now distributed as per-arch APT packages from AMD's
# packages-multi-arch repo: amdrocm-blas7.13-gfx1201 ships the gfx1201 Tensile/rocBLAS kernels
# directly (no need to pull the multi-GB rocm/vllm image just to harvest tuned kernels). We
# install ROCm 7.13 from APT in the build stage and copy just the runtime libs + llama.cpp
# binaries into a slim final image.
#
# Structure follows the official llama.cpp .devops/rocm.Dockerfile, with two gfx1201 fixes that
# double decode: the gfx120X-TUNED ROCm 7.13 (vs upstream's general 7.2.1 -> 34 vs 18 tok/s) and
# ROCWMMA flash-attn OFF (it's slower on gfx12, #15021). We also build a single gfx1201 target and
# slim the runtime to only the needed libs.
#
# 2026 best-practices: multi-stage, digest-pinned bases, OCI labels, minimal runtime (no build
# toolchain). The image defaults to a non-root user; the k8s deploy overrides to root for ROCm
# device access (see the llama-server HelmRelease), matching the cluster's other GPU pods.
#
# NOTE on the APT layout: AMD's packages-multi-arch debs install into a single merged tree at
# /opt/rocm/core-7.13 (NOT the classic flat /opt/rocm, and NOT the rocm-sdk wheel layout). The
# gfx1201 kernels, HIP runtime, rocBLAS/hipBLASLt and the LLVM/clang toolchain all live under
# that one root, so build + staging both key off ${ROCM_PATH}.

# ---------------------------------------------------------------------------------------------
# Build stage — install AMD's gfx120X-tuned ROCm 7.13 from APT (hipcc + tuned rocBLAS/hipBLASLt
# + headers), then build llama.cpp against it.
FROM docker.io/library/ubuntu:26.04@sha256:f3d28607ddd78734bb7f71f117f3c6706c666b8b76cbff7c9ff6e5718d46ff64 AS build

# ROCm version — manually updated; check https://rocm.docs.amd.com for new releases.
# 7.13 is AMD's gfx120X-tuned (TheRock) preview series; the version is baked into the package
# names (amdrocm*7.13*) and the merged install root (/opt/rocm/core-7.13), so it appears below
# as a literal rather than only as this ARG.
ARG ROCM_VERSION=7.13.0
ENV ROCM_PATH=/opt/rocm/core-7.13

ENV DEBIAN_FRONTEND=noninteractive

# Install ROCm 7.13 (gfx1201-tuned) from AMD's packages-multi-arch APT repo. The repo path is
# .../ubuntu2404 with the distribution codename `stable` (it is NOT a per-Ubuntu-codename repo,
# and it is NOT mirrored on repo.radeon.com, which tops out at 7.2.x). Packages:
#   amdrocm7.13-gfx1201           -> runtime metapackage: HIP runtime, LLVM/clang, hipcc, and the
#                                    gfx1201-TUNED rocBLAS/hipBLASLt + Tensile kernels.
#   amdrocm-runtime-dev7.13       -> HIP runtime headers + hipcc dev files.
#   amdrocm-blas-dev7.13 + amdrocm-hipblas-common-dev7.13 -> rocBLAS/hipBLAS headers.
#   amdrocm-llvm-dev7.13          -> clang/LLVM dev files for the HIP compile.
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget gnupg ca-certificates \
    && mkdir -p /etc/apt/keyrings \
    && wget -qO - https://repo.amd.com/rocm/packages/gpg/rocm.gpg \
         | gpg --dearmor | tee /etc/apt/keyrings/amdrocm.gpg > /dev/null \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/amdrocm.gpg] https://repo.amd.com/rocm/packages-multi-arch/ubuntu2404 stable main" \
         | tee /etc/apt/sources.list.d/rocm.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        amdrocm7.13-gfx1201 \
        amdrocm-runtime-dev7.13 \
        amdrocm-blas-dev7.13 \
        amdrocm-hipblas-common-dev7.13 \
        amdrocm-llvm-dev7.13 \
    && rm -rf /var/lib/apt/lists/*

# Single gfx1201 target = smaller, faster build. ROCWMMA flash-attn is slower on gfx12 (#15021),
# keep it OFF. OpenSSL ON so the server can fetch GGUFs via -hf into the model-cache PVC.
ARG GGML_HIP_ROCWMMA_FATTN=OFF
ARG AMDGPU_TARGETS=gfx1201
# Latest llama.cpp release; Renovate bumps this tag.
# renovate: datasource=github-releases depName=ggml-org/llama.cpp versioning=regex:^b(?<patch>\d+)$
ARG LLAMA_CPP_VERSION=b9626
# Compile parallelism, set by the build workflow from the runner's ENFORCED memory limit. -j$(nproc)
# (=12) peaks past the 24Gi builder cap on the ggml-cuda template TUs and OOM-kills the runner.
ARG BUILD_JOBS=4

# Standard merged ROCm root (see header NOTE): compiler, HIP runtime, headers and the gfx1201
# Tensile kernels all live under ${ROCM_PATH}. `rocm-sdk path` is gone — paths are plain dirs.
ENV HIP_PATH=${ROCM_PATH} \
    HIPCXX=${ROCM_PATH}/lib/llvm/bin/clang++ \
    PATH=${ROCM_PATH}/bin:${ROCM_PATH}/lib/llvm/bin:${PATH}

RUN apt-get update \
    && apt-get install -y --no-install-recommends git cmake ninja-build build-essential libssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch "${LLAMA_CPP_VERSION}" https://github.com/ggml-org/llama.cpp . \
    && cmake -S . -B build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_HIP=ON \
        -DAMDGPU_TARGETS="${AMDGPU_TARGETS}" \
        -DGPU_TARGETS="${AMDGPU_TARGETS}" \
        -DGGML_HIP_ROCWMMA_FATTN="${GGML_HIP_ROCWMMA_FATTN}" \
        -DLLAMA_OPENSSL=ON \
        -DLLAMA_BUILD_TESTS=OFF \
        -DCMAKE_PREFIX_PATH="${ROCM_PATH}" \
    && cmake --build build --config Release -j"${BUILD_JOBS}" --target llama-server \
    && mkdir -p /opt/llama/bin /opt/llama/lib \
    && cp build/bin/llama-server /opt/llama/bin/ \
    && ldd /opt/llama/bin/llama-server | grep -q libssl \
         || { echo 'FATAL: llama-server built without OpenSSL — HTTPS/-hf downloads will fail'; exit 1; } \
    && find build -name '*.so*' -exec cp -P {} /opt/llama/lib/ \;

# Stage the minimal ROCm runtime the binaries link against (HIP + tuned rocBLAS/hipBLASLt + the
# gfx1201 Tensile kernel data + libLLVM). The APT layout already merges everything under
# ${ROCM_PATH}/lib, so we copy that one subtree (preserving the rocblas/hipblaslt kernel-data
# layout) and drop only the compiler binaries + cmake dir that runtime doesn't need.
RUN mkdir -p /opt/rocm-runtime \
    && cp -a ${ROCM_PATH}/lib /opt/rocm-runtime/lib \
    # KEEP llvm/lib — libLLVM/libclang-cpp are a real runtime dep (hipBLASLt -> rocRoller -> LLVM).
    # Drop only the compiler binaries + the cmake dir (not needed at runtime).
    && rm -rf /opt/rocm-runtime/lib/llvm/bin /opt/rocm-runtime/lib/cmake \
    # Fail LOUD if AMD's APT layout moved (the ${ROCM_PATH} root is tied to the 7.13 package set):
    # a silent miss would otherwise ship a runtime that crashes at first inference.
    && for lib in libamdhip64 librocblas libhipblaslt; do \
         test -n "$(find /opt/rocm-runtime -name "${lib}.so*" -print -quit)" \
           || { echo "FATAL: ${lib} not staged — ROCm APT layout changed"; exit 1; }; \
       done \
    && test -n "$(find /opt/rocm-runtime/lib/llvm/lib -name 'libLLVM.so*' -print -quit)" \
         || { echo "FATAL: libLLVM not staged — rocRoller/hipBLASLt will fail at runtime"; exit 1; }

# ---------------------------------------------------------------------------------------------
# Runtime stage — slim Ubuntu + only the ROCm runtime libs + llama.cpp binaries. No toolchain.
FROM docker.io/library/ubuntu:26.04@sha256:f3d28607ddd78734bb7f71f117f3c6706c666b8b76cbff7c9ff6e5718d46ff64 AS runtime

# Runtime system deps confirmed via ldd of llama-server + libamdhip64/librocblas/libhipblaslt:
# gcc runtime (libstdc++/atomic/gcc_s/gomp), openssl (HTTPS for -hf; llama.cpp uses cpp-httplib,
# not libcurl, since b95xx), numa, ca-certs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgomp1 libstdc++6 libatomic1 libgcc-s1 libssl3 libnuma1 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    # Non-root default user. Auto-assigned ids (not -u/-g 1000): ubuntu 24.04+ already holds 1000
    # with its default 'ubuntu' user. The k8s deploy overrides to root anyway, so the id is cosmetic.
    && groupadd llama && useradd -g llama -m -s /usr/sbin/nologin llama

COPY --from=build /opt/llama/bin/  /app/
COPY --from=build /opt/llama/lib/  /app/lib/
COPY --from=build /opt/rocm-runtime/ /opt/rocm-runtime/

# /dev/kfd + /dev/dri are provided by the pod (squat.ai/dri); groups 'video'(44)/'render'(226)
# are added via the pod securityContext. ROCBLAS/hipBLASLt find their gfx1201 kernels via LD path.
ENV LD_LIBRARY_PATH=/app/lib:/opt/rocm-runtime/lib:/opt/rocm-runtime/lib/llvm/lib \
    LLAMA_CACHE=/cache \
    HIP_VISIBLE_DEVICES=0

# OCI image annotations (supersede legacy LABEL schema).
LABEL org.opencontainers.image.title="llama-cpp-rocm-gfx1201" \
      org.opencontainers.image.description="llama.cpp server built for AMD RDNA4 gfx1201 (ROCm 7.13 gfx120X-tuned, APT), with MTP" \
      org.opencontainers.image.source="https://github.com/tanguille/cluster" \
      org.opencontainers.image.licenses="MIT"

USER llama
EXPOSE 8080
ENTRYPOINT ["/app/llama-server"]
CMD ["--help"]
