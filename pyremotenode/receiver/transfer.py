import binascii
import logging
import os
import stat
import struct

from pprint import pformat


def reconstruct_files(input_files: list):
    """

    Args:
        input_files:

    Returns:

    """
    logging.debug("Got the following files:\n{}".format(pformat(input_files)))
    header_format = "!iB{}sLLL"
    header_init_read = "!iB"
    header_init_length = struct.calcsize(header_init_read)
    header_max_length = struct.calcsize(header_format.format(255))
    continuation_format = "!i"
    continuation_length = struct.calcsize(continuation_format)
    indexes_format = "!LLL"
    indexes_length = struct.calcsize(indexes_format)

    crc_details = dict(
        headers=list(),
        segments=dict(),
    )

    for fn in input_files:
        file_length = os.stat(fn)[stat.ST_SIZE]
        if file_length < min(header_max_length, continuation_length):
            logging.debug("Skipping {} as not long enough to contain any header".format(fn))
            continue

        logging.debug("Reading {} of {} bytes".format(fn, file_length))

        # Start by reading the header information and filtering valid associations
        # between segments and the file headers received
        try:
            with open(fn, "rb") as fh:
                header_content = fh.read(header_init_length)
                crc_match, fn_length = struct.unpack(header_init_read, header_content)
                header_filename = fh.read(fn_length)

                # This is definitely not a header information chunk, so is a potential
                # segment chunk. We assume it is until we've received everything
                if binascii.crc32(header_filename) & 0xffff != crc_match:
                    logging.debug("{} byte string not the filename - crc mismatch".format(len(header_filename)))
                    fh.seek(continuation_length)
                    total_length, start_offset, end_offset = \
                        struct.unpack(indexes_format, fh.read(indexes_length))

                    if crc_match not in crc_details["segments"]:
                        crc_details["segments"][crc_match] = list()
                    crc_details["segments"][crc_match].append(dict(
                        fn=fn, start=start_offset, end=end_offset
                    ))
                    continue

                # The processing for a non-header chunk
                total_length, start_offset, end_offset = \
                    struct.unpack(indexes_format, fh.read(indexes_length))

                logging.info("{} detected".format(header_filename))
                crc_details["headers"].append((
                    header_filename, crc_match, total_length))

                if crc_match not in crc_details["segments"]:
                    crc_details["segments"][crc_match] = list()
                crc_details["segments"][crc_match].append(dict(
                    fn=fn, start=start_offset, end=end_offset
                ))

        except (struct.error, os.error) as e:
            logging.warning("Abandoning reading potential headers: {}".format(e))
            continue

    # Filter segments by identifying those that don't have a related header record
    valid_segment_keys = [df[1] for df in crc_details["headers"]]
    crc_details["segments"] = {k: v for k, v in crc_details["segments"].items() if k in valid_segment_keys}

    # Go through the headers and try and output data files
    for data_fn, seg_key, total_length in crc_details["headers"]:
        total_segments_length = sum([df["end"] - df["start"] for df in crc_details["segments"][seg_key]])
        if total_segments_length != total_length:
            logging.warning("Mismatch of length, four segments length {} does not match {}".
                            format(total_segments_length, total_length))
            continue

        logging.info("Attempting {} reconstruction from {} segments".format(
            data_fn, len(crc_details["segments"][seg_key])))

    return []
