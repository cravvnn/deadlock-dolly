#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "dolly_depth_sequence.hpp"
#include <array>
#include <cstring>
#include <ostream>
#include <streambuf>
#include <string>

namespace dolly::depth {
namespace {
// Write directly to a CREATE_NEW handle, retaining exclusive creation through
// the entire write. A standard ofstream would reopen/truncate a reserved path.
class FileBuffer final : public std::streambuf {
    HANDLE handle;
    std::array<char, 65536> bytes{};
    std::uint64_t position = 0;
    bool flush() {
        const auto length = static_cast<DWORD>(pptr() - pbase());
        DWORD written = 0;
        while (written < length) {
            DWORD part = 0;
            if (!WriteFile(handle, pbase() + written, length - written, &part, nullptr) || !part)
                return false;
            written += part;
        }
        position += length;
        setp(bytes.data(), bytes.data() + bytes.size());
        return true;
    }
    int_type overflow(int_type value) override {
        if (!flush())
            return traits_type::eof();
        if (!traits_type::eq_int_type(value, traits_type::eof())) {
            *pptr() = traits_type::to_char_type(value);
            pbump(1);
        }
        return traits_type::not_eof(value);
    }
    int sync() override { return flush() ? 0 : -1; }
    pos_type seekoff(off_type offset, std::ios_base::seekdir way,
                     std::ios_base::openmode which) override {
        if (offset == 0 && way == std::ios_base::cur && (which & std::ios_base::out))
            return pos_type(position + (pptr() - pbase()));
        return pos_type(off_type(-1));
    }

public:
    explicit FileBuffer(HANDLE value) : handle(value) {
        setp(bytes.data(), bytes.data() + bytes.size());
    }
};
std::wstring filename(std::uint64_t index) {
    auto number = std::to_wstring(index);
    if (number.size() < 8)
        number.insert(0, 8 - number.size(), L'0');
    return number + L".exr";
}
}
struct Sequence::Impl {
    std::wstring directory;
    std::uint64_t written = 0, last_sample = 0, last_pts = 0;
    std::uint32_t width = 0, height = 0;
    bool owned = false, complete = false;
    long failure = 0;
    std::vector<float> linear;
    bool fail(DWORD code) noexcept {
        if (!failure)
            failure = HRESULT_FROM_WIN32(code ? code : ERROR_WRITE_FAULT);
        return false;
    }
    template <class Write> bool file(const std::wstring& name, Write write) {
        const auto path = directory + L"\\" + name;
        const auto temporary = path + L".part";
        const HANDLE handle = CreateFileW(temporary.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                                          FILE_ATTRIBUTE_NORMAL, nullptr);
        if (handle == INVALID_HANDLE_VALUE)
            return fail(GetLastError());
        bool good = false;
        try {
            FileBuffer buffer(handle);
            std::ostream stream(&buffer);
            good = write(stream);
            stream.flush();
            good = good && stream.good();
        } catch (...) {
            good = false;
        }
        if (!CloseHandle(handle))
            good = false;
        if (good && MoveFileExW(temporary.c_str(), path.c_str(), 0))
            return true;
        const auto error = good ? GetLastError() : ERROR_WRITE_FAULT;
        // Only this function's atomically created temporary file is removed.
        DeleteFileW(temporary.c_str());
        return fail(error);
    }
};
Sequence::Sequence() : impl(std::make_unique<Impl>()) {}
Sequence::~Sequence() {
    if (!impl->complete)
        discard();
}
bool Sequence::begin(const wchar_t* video_path) noexcept {
    try {
        if (!video_path || !*video_path || !impl->directory.empty())
            return impl->fail(ERROR_INVALID_PARAMETER);
        const std::wstring path(video_path);
        if (path.size() > 1023 ||
            !((path.size() > 3 && path[1] == L':' && (path[2] == L'/' || path[2] == L'\\')) ||
              (path.size() > 3 && path[0] == L'\\' && path[1] == L'\\')))
            return impl->fail(ERROR_INVALID_PARAMETER);
        impl->directory = path + L".depth";
        if (!CreateDirectoryW(impl->directory.c_str(), nullptr))
            return impl->fail(GetLastError());
        impl->owned = true;
        return true;
    } catch (...) {
        return impl->fail(ERROR_OUTOFMEMORY);
    }
}
bool Sequence::write(const RawFrame& raw, std::uint64_t pts) noexcept {
    try {
        auto& s = *impl;
        const auto& frame = raw.frame;
        if (!s.owned || s.complete || s.failure ||
            (s.written && (frame.width != s.width || frame.height != s.height ||
                           frame.sample <= s.last_sample || pts <= s.last_pts)))
            return s.fail(ERROR_INVALID_DATA);
        const auto count = std::uint64_t(frame.width) * frame.height;
        if (!count || count > 3840ULL * 2160 || raw.pixels.size() != count * 4)
            return s.fail(ERROR_INVALID_DATA);
        s.linear.resize(static_cast<std::size_t>(count));
        if (!convert(raw.pixels.data(), raw.pixels.size(), std::size_t(frame.width) * 4,
                     frame.width, frame.height, raw.format, frame.projection, s.linear.data(),
                     s.linear.size()))
            return s.fail(ERROR_INVALID_DATA);
        auto metadata = frame;
        metadata.capture_pts_100ns = pts;
        if (!s.file(filename(s.written), [&](std::ostream& out) {
                return write_exr(out, metadata, s.linear.data(), s.linear.size());
            }))
            return false;
        s.width = frame.width;
        s.height = frame.height;
        s.last_sample = frame.sample;
        s.last_pts = pts;
        ++s.written;
        return true;
    } catch (...) {
        return impl->fail(ERROR_OUTOFMEMORY);
    }
}
bool Sequence::finish(std::uint64_t encoded_color_frames) noexcept {
    try {
        auto& s = *impl;
        if (!s.owned || s.complete || s.failure || !s.written || s.written != encoded_color_frames)
            return s.fail(ERROR_INVALID_DATA);
        if (!s.file(L"manifest.json", [&](std::ostream& out) {
                out << "{\n  \"version\": 1,\n  \"frames\": " << s.written
                    << ",\n  \"width\": " << s.width << ",\n  \"height\": " << s.height
                    << ",\n  \"channel\": \"Z\",\n  \"type\": \"FLOAT\","
                       "\n  \"units\": \"positive camera-axis game units\","
                       "\n  \"order\": \"zero-based encoded color frame index\","
                       "\n  \"capture_pts\": \"dollyCapturePTS100ns records capture time; encoded video timing may differ\"\n}\n";
                return out.good();
            }))
            return false;
        s.complete = true;
        return true;
    } catch (...) {
        return impl->fail(ERROR_OUTOFMEMORY);
    }
}
void Sequence::discard() noexcept {
    auto& s = *impl;
    if (!s.owned)
        return;
    try {
        for (std::uint64_t i = 0; i < s.written; ++i)
            DeleteFileW((s.directory + L"\\" + filename(i)).c_str());
        if (s.complete)
            DeleteFileW((s.directory + L"\\manifest.json").c_str());
        // No recursive deletion: foreign files in the directory are preserved.
        RemoveDirectoryW(s.directory.c_str());
        s.owned = false;
    } catch (...) {
    }
}
long Sequence::error() const noexcept {
    return impl->failure;
}
std::uint64_t Sequence::count() const noexcept {
    return impl->written;
}
}
